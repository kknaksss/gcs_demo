// Drive 연동 관리 — connector 상태 / Sync Activity / 문서 수집 결과
// (SPEC-004 U-1~U-3, WORK-003 Phase 4) + 문서별 AI 분석 상태 (SPEC-007 U-1,
// WORK-004 Phase 5). admin guard는 catalog 페이지 패턴과 동일.
// v1 감시 폴더 선택/변경은 GOOGLE_DRIVE_SELECTED_FOLDER_ID env로만 수행한다.
"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle, RefreshCw, ShieldAlert } from "lucide-react";

import { ClassificationStatusPanel } from "@/components/approval/classification-status";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { fetchMe, type Me } from "@/lib/api/organization";
import {
  fetchConnectorStatus,
  fetchSyncEvents,
  registerWatch,
  retrySync,
  spec004Message,
  type ConnectorStatus,
  type SyncEvent,
  type SyncRetryResult,
} from "@/lib/api/driveConnector";

const NAV = [
  { label: "문서", href: "/documents" },
  { label: "승인", href: "/admin/approvals" },
  { label: "관리", href: "/admin/catalog" },
  { label: "Drive 연동", href: "/admin/connector", active: true },
  { label: "로그인/RBAC", href: "/login" },
];

type Guard = "loading" | "admin" | "forbidden";

// ── SPEC-004 U-1 상태 표기 ───────────────────────────────────────────────────

const STATUS_LABEL: Record<ConnectorStatus["status"], string> = {
  connected: "연결됨",
  disconnected: "설정 필요",
  watch_expiring: "갱신 필요",
  error: "오류",
};

const STATUS_BADGE_CLASS: Record<ConnectorStatus["status"], string> = {
  connected:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  disconnected: "bg-muted text-muted-foreground",
  watch_expiring:
    "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  error: "bg-destructive/10 text-destructive",
};

// ── SPEC-004 U-3 intake 라벨 (sync events 기반) ──────────────────────────────

const EVENT_LABEL: Record<SyncEvent["event_type"], string> = {
  webhook_received: "webhook 수신",
  changes_listed: "변경 조회",
  document_upserted: "문서 반영",
  document_unavailable: "숨김 처리",
  candidate_staled: "후보 stale",
  reanalysis_enqueued: "재분석 요청",
  sync_failed: "동기화 실패",
};

function intakeLabel(event: SyncEvent): string {
  if (event.event_type === "document_upserted") {
    return event.message?.startsWith("new document") ? "새 문서" : "갱신된 문서";
  }
  return EVENT_LABEL[event.event_type];
}

function eventBadgeClass(event: SyncEvent): string {
  if (event.result === "failed") return "bg-destructive/10 text-destructive";
  if (event.event_type === "document_unavailable")
    return "bg-destructive/10 text-destructive";
  if (event.event_type === "document_upserted")
    return "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200";
  if (event.event_type === "candidate_staled")
    return "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200";
  return "bg-muted text-muted-foreground";
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("ko-KR");
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

function KvRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{children}</span>
    </div>
  );
}

export default function ConnectorPage() {
  const router = useRouter();
  const [guard, setGuard] = useState<Guard>("loading");
  const [me, setMe] = useState<Me | null>(null);

  const [status, setStatus] = useState<ConnectorStatus | null>(null);
  const [events, setEvents] = useState<SyncEvent[]>([]);
  const [eventsTotal, setEventsTotal] = useState(0);
  const [lastRetry, setLastRetry] = useState<SyncRetryResult | null>(null);
  const [loadingData, setLoadingData] = useState(true);
  const [busy, setBusy] = useState<"watch" | "retry" | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);

  const reload = useCallback(async () => {
    setLoadingData(true);
    setPageError(null);
    try {
      const [connector, eventList] = await Promise.all([
        fetchConnectorStatus(),
        fetchSyncEvents(30),
      ]);
      setStatus(connector);
      setEvents(eventList.events);
      setEventsTotal(eventList.total);
      setNotConfigured(connector.status === "disconnected");
    } catch (err) {
      setPageError(spec004Message(err, "연동 상태를 불러오지 못했습니다."));
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

  async function handleRegisterWatch() {
    setBusy("watch");
    setPageError(null);
    try {
      const next = await registerWatch();
      setStatus(next);
    } catch (err) {
      setPageError(spec004Message(err, "watch 갱신에 실패했습니다."));
    } finally {
      setBusy(null);
    }
  }

  async function handleRetrySync() {
    setBusy("retry");
    setPageError(null);
    try {
      const result = await retrySync();
      setLastRetry(result);
      await reload();
    } catch (err) {
      setPageError(spec004Message(err, "Drive 변경 처리에 실패했습니다."));
    } finally {
      setBusy(null);
    }
  }

  // ── 문서 수집 결과 집계 (U-3) — 현재 페이지 sync events 기반 ────────────────
  const intake = useMemo(() => {
    let created = 0;
    let updated = 0;
    let unavailable = 0;
    let metadataOnly = 0;
    for (const e of events) {
      if (e.event_type === "document_upserted") {
        if (e.message?.startsWith("new document")) created += 1;
        else updated += 1;
      } else if (e.event_type === "document_unavailable") {
        unavailable += 1;
      }
      if (e.message?.includes("metadata_only")) metadataOnly += 1;
    }
    return { created, updated, unavailable, metadataOnly };
  }, [events]);

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
              Drive 연동 관리는 admin 계정에서만 접근할 수 있습니다.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const failed = status?.status === "error";

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

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-xs text-muted-foreground">
              관리 / Google Drive 연동 · Sync Activity · 수집 결과
            </div>
            <h1 className="text-xl font-semibold">Drive 연동 관리</h1>
          </div>
          <Button variant="outline" onClick={() => void reload()} disabled={loadingData}>
            {loadingData ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <RefreshCw />
            )}
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

        {notConfigured && (
          <div className="rounded-lg border border-amber-300/50 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            Drive 연동 설정이 필요합니다. `GOOGLE_DRIVE_*` env 5종을 설정한 뒤 상태를
            새로고침하세요.
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          {/* ── ① Google Drive 연동 (U-1) ── */}
          <Panel
            title="Google Drive 연동"
            badge={
              status && (
                <span
                  className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_BADGE_CLASS[status.status]}`}
                >
                  {STATUS_LABEL[status.status]}
                </span>
              )
            }
          >
            <div className="flex flex-col px-4 py-3">
              <KvRow label="scope">
                <span className="font-mono text-xs">{status?.scope ?? "drive.readonly"}</span>
              </KvRow>
              <KvRow label="감시 폴더">
                {status?.selected_folder_name ??
                  status?.selected_folder_id ??
                  "미설정"}
              </KvRow>
              <KvRow label="watch channel">
                {status?.watch_channel_id ? (
                  <span className="font-mono text-xs">{status.watch_channel_id}</span>
                ) : (
                  "미등록"
                )}
              </KvRow>
              <KvRow label="watch 만료">
                {formatDateTime(status?.watch_expires_at ?? null)}
              </KvRow>
            </div>
            <div className="flex flex-col gap-2 border-t border-border p-4">
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => void handleRegisterWatch()}
                  disabled={busy !== null || notConfigured}
                >
                  {busy === "watch" && <LoaderCircle className="animate-spin" />}
                  watch 갱신
                </Button>
                {status?.status === "watch_expiring" && (
                  <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-200">
                    갱신 필요
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                v1 감시 폴더 선택/변경은 관리자 화면이 아니라
                `GOOGLE_DRIVE_SELECTED_FOLDER_ID` env 변경으로 수행한다. 이전 폴더에만
                속한 문서는 out_of_scope로 숨긴다.
              </p>
            </div>
          </Panel>

          {/* ── ② Sync Activity (U-2) ── */}
          <Panel
            title="Sync Activity"
            badge={
              <span
                className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  failed
                    ? "bg-destructive/10 text-destructive"
                    : "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200"
                }`}
              >
                {failed ? "failed" : "idle"}
              </span>
            }
          >
            <div className="flex flex-col px-4 py-3">
              <KvRow label="마지막 동기화">
                {formatDateTime(status?.last_sync_at ?? null)}
              </KvRow>
              <KvRow label="마지막 변경 토큰">
                {status?.page_token ? (
                  <span className="font-mono text-xs">{status.page_token}</span>
                ) : (
                  "—"
                )}
              </KvRow>
              <KvRow label="처리된 변경 수">
                {lastRetry ? `${lastRetry.processed}` : "—"}
              </KvRow>
            </div>
            <div className="flex flex-col gap-2 border-t border-border p-4">
              {failed && status?.last_error && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                  Drive 변경 처리에 실패했습니다.
                  <div className="mt-1 font-mono">{status.last_error}</div>
                </div>
              )}
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant={failed ? "default" : "outline"}
                  onClick={() => void handleRetrySync()}
                  disabled={busy !== null || notConfigured}
                >
                  {busy === "retry" && <LoaderCircle className="animate-spin" />}
                  다시 처리
                </Button>
                <span className="text-xs text-muted-foreground">
                  같은 change 중복 처리에도 최종 상태는 같아야 한다
                </span>
              </div>
              {lastRetry && (
                <p className="text-xs text-muted-foreground">
                  최근 처리: 새 문서 {lastRetry.new_documents} · 갱신{" "}
                  {lastRetry.updated_documents} · 숨김{" "}
                  {lastRetry.unavailable_documents} · 건너뜀 {lastRetry.skipped} ·
                  실패 {lastRetry.failed}
                </p>
              )}
            </div>
          </Panel>
        </div>

        {/* ── ③ 문서 수집 결과 (U-3) ── */}
        <Panel
          title="문서 수집 결과"
          badge={
            <a
              href="/admin/approvals"
              className="rounded-md bg-foreground px-2.5 py-1 text-xs font-medium text-background"
            >
              승인 게이트로 이동
            </a>
          }
        >
          <table className="w-full text-left text-sm">
            <thead className="bg-muted/50 text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium">구분</th>
                <th className="px-3 py-2 font-medium">건수</th>
                <th className="px-3 py-2 font-medium">설명</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-border">
                <td className="px-3 py-2">
                  <span className="inline-flex items-center rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
                    새 문서
                  </span>
                </td>
                <td className="px-3 py-2">{intake.created}</td>
                <td className="px-3 py-2 text-muted-foreground">
                  새 Drive file이 document record로 등록됨
                </td>
              </tr>
              <tr className="border-t border-border">
                <td className="px-3 py-2">
                  <span className="inline-flex items-center rounded-full bg-sky-100 px-2.5 py-0.5 text-xs font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-200">
                    갱신된 문서
                  </span>
                </td>
                <td className="px-3 py-2">{intake.updated}</td>
                <td className="px-3 py-2 text-muted-foreground">
                  기존 document mirror가 갱신됨
                </td>
              </tr>
              <tr className="border-t border-border">
                <td className="px-3 py-2">
                  <span className="inline-flex items-center rounded-full bg-destructive/10 px-2.5 py-0.5 text-xs font-medium text-destructive">
                    숨김 처리
                  </span>
                </td>
                <td className="px-3 py-2">{intake.unavailable}</td>
                <td className="px-3 py-2 text-muted-foreground">
                  trashed / removed / out_of_scope
                </td>
              </tr>
              <tr className="border-t border-border">
                <td className="px-3 py-2">
                  <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-200">
                    본문 분석 없음
                  </span>
                </td>
                <td className="px-3 py-2">{intake.metadataOnly}</td>
                <td className="px-3 py-2 text-muted-foreground">
                  본문 분석 없이 metadata 후보만 생성됨 (WORK-004에서 채워짐)
                </td>
              </tr>
            </tbody>
          </table>
        </Panel>

        {/* ── 문서별 AI 분석 상태 (SPEC-007 U-1 — WORK-004) ── */}
        <ClassificationStatusPanel />

        {/* ── sync events 감사 목록 ── */}
        <Panel
          title="Sync Events"
          badge={
            <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
              최신 {events.length} / 총 {eventsTotal}
            </span>
          }
        >
          {events.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">
              아직 기록된 sync event가 없습니다. Drive 선택 폴더에 파일을 넣거나
              `다시 처리`를 실행하세요.
            </p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">구분</th>
                  <th className="px-3 py-2 font-medium">문서</th>
                  <th className="px-3 py-2 font-medium">결과</th>
                  <th className="px-3 py-2 font-medium">요약</th>
                  <th className="px-3 py-2 font-medium">발생 시각</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id} className="border-t border-border">
                    <td className="px-3 py-2">
                      <span
                        className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${eventBadgeClass(event)}`}
                      >
                        {intakeLabel(event)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {event.document_id !== null
                        ? `#${event.document_id}`
                        : (event.drive_file_id ?? "—")}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{event.result}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {event.message ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {formatDateTime(event.occurred_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </main>
    </div>
  );
}
