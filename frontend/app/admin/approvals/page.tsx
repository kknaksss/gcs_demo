// 승인 게이트 — AI 후보 검토/승인/거절 (SPEC-005, admin 전용). WORK-005 Phase 3.
// 시안: kknaks_profile 21-html/page-approvals.html — 필터 6종, 원장 5개 +
// 표시용 재분석 상태 2축 badge, stale/blocked/metadata_only 배너, CTA 활성 규칙
// (승인=pending만, 거절=pending/stale, 재분석=재분석 실패 시).
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ExternalLink,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  MetadataForm,
  type MetadataFormHandle,
} from "@/components/approval/metadata-form";
import { RelationSection } from "@/components/relation/relation-section";
import { ApiError } from "@/lib/api/client";
import {
  approveCandidate,
  BLOCKED_MESSAGE,
  CANDIDATE_STATE_LABEL,
  EMPTY_QUEUE_MESSAGE,
  fetchAdminDocumentTypes,
  fetchApprovalCandidate,
  fetchApprovalCandidates,
  METADATA_ONLY_MESSAGE,
  reanalyzeCandidate,
  REANALYSIS_STATUS_LABEL,
  rejectCandidate,
  spec005Message,
  STALE_MESSAGE,
  type AdminDocumentTypeItem,
  type ApprovalCandidateDetail,
  type ApprovalCandidateSummary,
  type CandidateState,
  type ReanalysisStatus,
} from "@/lib/api/approvals";
import {
  fetchDocumentTreeConfig,
  fetchMe,
  fetchOrganizationTree,
  type Me,
  type OrgNode,
  type TreeNode,
} from "@/lib/api/organization";

const NAV = [
  { label: "문서", href: "/documents" },
  { label: "승인", href: "/admin/approvals", active: true },
  { label: "관리", href: "/admin/catalog" },
  { label: "Drive 연동", href: "/admin/connector" },
  { label: "로그인/RBAC", href: "/login" },
];

const POLL_INTERVAL_MS = 10_000;

type Guard = "loading" | "admin" | "forbidden";

// SPEC-005 U-1 filters: 전체 / 승인 대기 / stale / 재분석 중 / 차단됨 / 본문 분석 없음
type QueueFilter =
  | "all"
  | "pending"
  | "stale"
  | "reanalyzing"
  | "blocked"
  | "metadata_only";

const FILTERS: { key: QueueFilter; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "pending", label: "승인 대기" },
  { key: "stale", label: "stale" },
  { key: "reanalyzing", label: "재분석 중" },
  { key: "blocked", label: "차단됨" },
  { key: "metadata_only", label: "본문 분석 없음" },
];

const STATE_BADGE: Record<CandidateState, string> = {
  pending: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  stale: "bg-destructive/10 text-destructive",
  approved:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  rejected: "bg-muted text-muted-foreground",
  blocked: "bg-destructive/10 text-destructive",
};

const REANALYSIS_BADGE: Record<ReanalysisStatus, string> = {
  reanalyzing: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  new_candidate_ready:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  reanalysis_failed: "bg-destructive/10 text-destructive",
};

function StateBadge({ state }: { state: CandidateState }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATE_BADGE[state]}`}
    >
      {CANDIDATE_STATE_LABEL[state]}
    </span>
  );
}

function DerivedBadges({
  candidate,
}: {
  candidate: ApprovalCandidateSummary;
}) {
  return (
    <>
      {candidate.reanalysis_status && (
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${REANALYSIS_BADGE[candidate.reanalysis_status]}`}
        >
          {REANALYSIS_STATUS_LABEL[candidate.reanalysis_status]}
        </span>
      )}
      {candidate.read_capability === "metadata_only" && (
        <span className="inline-flex items-center rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-200">
          본문 분석 없음
        </span>
      )}
    </>
  );
}

export default function ApprovalsPage() {
  const router = useRouter();
  const [guard, setGuard] = useState<Guard>("loading");
  const [me, setMe] = useState<Me | null>(null);

  const [filter, setFilter] = useState<QueueFilter>("all");
  const [candidates, setCandidates] = useState<ApprovalCandidateSummary[]>([]);
  const [queueLoading, setQueueLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ApprovalCandidateDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [orgNodes, setOrgNodes] = useState<OrgNode[]>([]);
  const [treeNodes, setTreeNodes] = useState<TreeNode[]>([]);
  const [docTypes, setDocTypes] = useState<AdminDocumentTypeItem[]>([]);

  const [pageError, setPageError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);

  const formRef = useRef<MetadataFormHandle | null>(null);
  const formTopRef = useRef<HTMLDivElement | null>(null);

  // ── data loading ───────────────────────────────────────────────────────────

  const reloadQueue = useCallback(async (current: QueueFilter) => {
    try {
      const params =
        current === "all"
          ? {}
          : current === "metadata_only"
            ? { read_capability: "metadata_only" as const }
            : current === "reanalyzing"
              ? { state: "stale" as const }
              : { state: current };
      const res = await fetchApprovalCandidates(params);
      const rows =
        current === "reanalyzing"
          ? res.candidates.filter(
              (c) => c.reanalysis_status === "reanalyzing",
            )
          : res.candidates;
      setCandidates(rows);
      setPageError(null);
    } catch (err) {
      setPageError(spec005Message(err, "후보 목록을 불러오지 못했습니다."));
    } finally {
      setQueueLoading(false);
    }
  }, []);

  const reloadDetail = useCallback(async (candidateId: number) => {
    setDetailLoading(true);
    try {
      const res = await fetchApprovalCandidate(candidateId);
      setDetail(res);
    } catch (err) {
      setDetail(null);
      setPageError(spec005Message(err, "후보 상세를 불러오지 못했습니다."));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const reloadReference = useCallback(async () => {
    const [orgs, tree, types] = await Promise.all([
      fetchOrganizationTree(),
      fetchDocumentTreeConfig(),
      fetchAdminDocumentTypes(),
    ]);
    setOrgNodes(orgs);
    setTreeNodes(tree);
    setDocTypes(types);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((user) => {
        if (cancelled) return;
        setMe(user);
        if (user.is_admin) {
          setGuard("admin");
          void reloadQueue("all");
          void reloadReference().catch(() =>
            setPageError("조직/카탈로그 데이터를 불러오지 못했습니다."),
          );
        } else {
          // 비admin은 접근 차단 (FORBIDDEN_ADMIN_ONLY)
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
  }, [router, reloadQueue, reloadReference]);

  // queue polling — stale/재분석 표시 상태 갱신 (DEC-022)
  useEffect(() => {
    if (guard !== "admin") return;
    const timer = setInterval(() => void reloadQueue(filter), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [guard, filter, reloadQueue]);

  useEffect(() => {
    if (selectedId !== null) void reloadDetail(selectedId);
  }, [selectedId, reloadDetail]);

  // ── actions (U-2 CTA) ────────────────────────────────────────────────────

  const canApprove =
    detail !== null && detail.state === "pending" && detail.fingerprint_match;
  const canReject =
    detail !== null && (detail.state === "pending" || detail.state === "stale");
  const canReanalyze =
    detail !== null && detail.reanalysis_status === "reanalysis_failed";

  async function handleApprove() {
    if (!detail || !formRef.current) return;
    setActionError(null);
    setActionNotice(null);
    setActionBusy(true);
    try {
      const payload = formRef.current.buildPayload();
      const res = await approveCandidate(detail.id, payload);
      setActionNotice(
        res.idempotent
          ? "이미 같은 내용으로 승인된 후보입니다."
          : "승인 완료 — approved metadata가 문서에 반영되었습니다.",
      );
      await Promise.all([reloadQueue(filter), reloadDetail(detail.id)]);
    } catch (err) {
      if (err instanceof Error && !(err instanceof ApiError)) {
        setActionError(err.message); // form 필수값 오류
      } else {
        setActionError(spec005Message(err, "승인에 실패했습니다."));
        if (err instanceof ApiError) void reloadDetail(detail.id);
      }
    } finally {
      setActionBusy(false);
    }
  }

  async function handleReject() {
    if (!detail) return;
    if (!window.confirm("이 후보를 거절할까요?")) return;
    setActionError(null);
    setActionNotice(null);
    setActionBusy(true);
    try {
      await rejectCandidate(detail.id);
      setActionNotice("후보를 거절했습니다.");
      await Promise.all([reloadQueue(filter), reloadDetail(detail.id)]);
    } catch (err) {
      setActionError(spec005Message(err, "거절에 실패했습니다."));
    } finally {
      setActionBusy(false);
    }
  }

  async function handleReanalyze() {
    if (!detail) return;
    setActionError(null);
    setActionNotice(null);
    setActionBusy(true);
    try {
      await reanalyzeCandidate(detail.id);
      setActionNotice("재분석을 요청했습니다. 완료되면 새 후보가 생성됩니다.");
      await Promise.all([reloadQueue(filter), reloadDetail(detail.id)]);
    } catch (err) {
      setActionError(spec005Message(err, "재분석 요청에 실패했습니다."));
    } finally {
      setActionBusy(false);
    }
  }

  const fingerprintShort = useMemo(() => {
    if (!detail) return null;
    const short = (fp: Record<string, unknown>) => {
      const time = fp["drive_modified_time"];
      return typeof time === "string" ? time : JSON.stringify(fp).slice(0, 24);
    };
    return {
      candidate: short(detail.candidate_fingerprint),
      current: short(detail.current_fingerprint),
    };
  }, [detail]);

  // ── guards ─────────────────────────────────────────────────────────────────

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
              승인 게이트는 admin 계정에서만 접근할 수 있습니다.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const formDisabled =
    actionBusy || detail === null || detail.state !== "pending";

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
          <span className="text-xs text-muted-foreground">{me.name} · admin</span>
        )}
      </header>

      <main className="flex flex-1 gap-6 p-6">
        {/* ── 후보 큐 (U-1) ── */}
        <aside className="flex w-80 shrink-0 flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold">승인 대기</h2>
              <span className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-200">
                admin only
              </span>
            </div>
            <Button
              size="icon-sm"
              variant="outline"
              aria-label="새로고침"
              onClick={() => void reloadQueue(filter)}
              disabled={queueLoading}
            >
              {queueLoading ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <RefreshCw />
              )}
            </Button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => {
                  setFilter(f.key);
                  setQueueLoading(true);
                  void reloadQueue(f.key);
                }}
                className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                  filter === f.key
                    ? "bg-foreground text-background"
                    : "border border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="flex flex-col gap-1.5 overflow-y-auto rounded-xl border border-border bg-card p-2">
            {queueLoading && candidates.length === 0 ? (
              <div className="flex flex-col gap-2 p-2">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-12 animate-pulse rounded-lg bg-muted" />
                ))}
              </div>
            ) : candidates.length === 0 ? (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                {EMPTY_QUEUE_MESSAGE}
              </p>
            ) : (
              candidates.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => setSelectedId(c.id)}
                  className={`rounded-lg px-3 py-2 text-left ${
                    selectedId === c.id
                      ? "bg-muted"
                      : "hover:bg-muted/50"
                  }`}
                >
                  <div className="truncate text-sm font-medium">
                    {c.drive_name}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <StateBadge state={c.state} />
                    <DerivedBadges candidate={c} />
                    <span className="text-xs text-muted-foreground">
                      candidate#{c.id}
                    </span>
                  </div>
                </button>
              ))
            )}
          </div>
        </aside>

        {/* ── 후보 상세 (U-2) ── */}
        <section className="flex min-w-0 flex-1 flex-col gap-4">
          {pageError && (
            <div
              role="alert"
              className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {pageError}
            </div>
          )}

          {detail === null ? (
            <div className="grid flex-1 place-items-center rounded-xl border border-dashed border-border text-sm text-muted-foreground">
              {detailLoading ? (
                <span className="flex items-center gap-2">
                  <LoaderCircle className="size-4 animate-spin" /> 불러오는 중…
                </span>
              ) : (
                "왼쪽 큐에서 후보를 선택하세요."
              )}
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-xs text-muted-foreground">
                    승인 게이트 / candidate#{detail.id} / {detail.drive_name}
                  </div>
                  <h1 className="truncate text-xl font-semibold">
                    {detail.drive_name}
                  </h1>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <StateBadge state={detail.state} />
                    <DerivedBadges candidate={detail} />
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {/* 승인/수정 후 승인 = pending에서만 (U-2) */}
                  <Button
                    onClick={() => void handleApprove()}
                    disabled={!canApprove || actionBusy}
                  >
                    {actionBusy && <LoaderCircle className="animate-spin" />}
                    승인
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!canApprove || actionBusy}
                    onClick={() =>
                      formTopRef.current?.scrollIntoView({
                        behavior: "smooth",
                        block: "start",
                      })
                    }
                  >
                    수정 후 승인
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => void handleReject()}
                    disabled={!canReject || actionBusy}
                  >
                    거절
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => void handleReanalyze()}
                    disabled={!canReanalyze || actionBusy}
                  >
                    재분석
                  </Button>
                  {detail.drive_web_url && (
                    <Button
                      variant="outline"
                      onClick={() =>
                        window.open(
                          detail.drive_web_url ?? "",
                          "_blank",
                          "noreferrer",
                        )
                      }
                    >
                      <ExternalLink /> Drive에서 열기
                    </Button>
                  )}
                </div>
              </div>

              {/* 상태 배너 (SPEC-005 U-2 문구) */}
              {detail.state === "stale" && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  <strong>{STALE_MESSAGE}</strong>
                  {detail.reanalysis_status === "reanalyzing" && (
                    <span className="ml-2 text-xs">
                      최신 Drive 기준으로 재분석 중 — 새 후보 준비 중입니다.
                    </span>
                  )}
                  {detail.reanalysis_status === "new_candidate_ready" && (
                    <span className="ml-2 text-xs">
                      새 pending 후보가 준비되었습니다. 큐에서 새 후보를
                      선택하세요.
                    </span>
                  )}
                  {detail.reanalysis_status === "reanalysis_failed" && (
                    <span className="ml-2 text-xs">
                      자동 재분석이 실패했습니다. 재분석 버튼으로 다시 요청할
                      수 있습니다.
                    </span>
                  )}
                </div>
              )}
              {detail.state === "blocked" && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  <strong>{BLOCKED_MESSAGE}</strong>
                  {detail.blocked_reason && (
                    <span className="ml-2 text-xs">{detail.blocked_reason}</span>
                  )}
                </div>
              )}
              {detail.read_capability === "metadata_only" && (
                <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-200">
                  {METADATA_ONLY_MESSAGE}
                </div>
              )}

              {/* fingerprint 재검사 요약 */}
              <div className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                <span>
                  candidate fingerprint:{" "}
                  <span className="font-mono">{fingerprintShort?.candidate}</span>
                </span>
                <span>
                  current mirror:{" "}
                  <span className="font-mono">{fingerprintShort?.current}</span>
                </span>
                <span>
                  판정:{" "}
                  <strong
                    className={
                      detail.fingerprint_match
                        ? "text-emerald-600 dark:text-emerald-300"
                        : "text-destructive"
                    }
                  >
                    {detail.fingerprint_match ? "match — 승인 가능" : "mismatch — 승인 차단"}
                  </strong>
                </span>
              </div>

              {actionError && (
                <div
                  role="alert"
                  className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                >
                  {actionError}
                </div>
              )}
              {actionNotice && (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
                  {actionNotice}
                </div>
              )}

              <div ref={formTopRef} />
              <MetadataForm
                key={`${detail.id}:${detail.updated_at}`}
                ref={formRef}
                detail={detail}
                orgNodes={orgNodes}
                treeNodes={treeNodes}
                docTypes={docTypes}
                onDocTypeCreated={(created) =>
                  setDocTypes((prev) =>
                    prev.some((t) => t.id === created.id)
                      ? prev
                      : [...prev, created].sort((a, b) =>
                          a.name.localeCompare(b.name, "ko"),
                        ),
                  )
                }
                disabled={formDisabled}
              />

              <RelationSection
                relations={detail.relation_candidates}
                sourceDocumentId={detail.document_id}
                disabled={actionBusy}
                onChanged={() => void reloadDetail(detail.id)}
              />
            </>
          )}
        </section>
      </main>
    </div>
  );
}
