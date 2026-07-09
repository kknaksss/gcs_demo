// 문서별 AI 분석 이력/상태 최소 표시 — WORK-004 Phase 5 (SPEC-007 U-1).
// DB job 상태 API를 폴링한다 (Redis 미접근 — ARCH-002 §6). 승인 게이트 통합
// 표시는 WORK-005 소관 — 여기는 admin 확인용 최소 표면이다.
"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { LoaderCircle, Play, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  CLASSIFICATION_STATUS_LABEL,
  classifyDocument,
  fetchAdminDocuments,
  fetchDocumentClassificationJobs,
  jobErrorMessage,
  spec007Message,
  type AdminDocumentSummary,
  type ClassificationJob,
  type ClassificationJobStatus,
} from "@/lib/api/classification";

const POLL_INTERVAL_MS = 10_000;
// 전체 문서 표시 (BE limit 상한 200 — 문서 수가 그 이상이면 집계 API 필요)
const DOCUMENT_LIMIT = 200;

const STATUS_BADGE_CLASS: Record<ClassificationJobStatus, string> = {
  queued: "bg-muted text-muted-foreground",
  running: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  succeeded: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  candidate_saved:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  validation_failed: "bg-destructive/10 text-destructive",
  failed: "bg-destructive/10 text-destructive",
  timeout: "bg-destructive/10 text-destructive",
  stale: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
};

function StatusBadge({ job }: { job: ClassificationJob | null }) {
  if (!job) {
    return (
      <span className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
        분석 이력 없음
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_BADGE_CLASS[job.status]}`}
    >
      {CLASSIFICATION_STATUS_LABEL[job.status]}
    </span>
  );
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("ko-KR");
}

interface RowState {
  document: AdminDocumentSummary;
  latest: ClassificationJob | null;
  history: ClassificationJob[];
}

export function ClassificationStatusPanel() {
  const [rows, setRows] = useState<RowState[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyDocId, setBusyDocId] = useState<number | null>(null);
  const [expandedDocId, setExpandedDocId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const list = await fetchAdminDocuments(DOCUMENT_LIMIT);
      const next = await Promise.all(
        list.documents.map(async (document) => {
          const jobs = await fetchDocumentClassificationJobs(document.id, 20);
          return {
            document,
            latest: jobs.jobs[0] ?? null,
            history: jobs.jobs,
          } satisfies RowState;
        }),
      );
      setRows(next);
      setError(null);
    } catch (err) {
      setError(spec007Message(err, "분석 상태를 불러오지 못했습니다."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    const timer = setInterval(() => void reload(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [reload]);

  async function handleClassify(documentId: number) {
    setBusyDocId(documentId);
    setError(null);
    try {
      await classifyDocument(documentId);
      await reload();
    } catch (err) {
      setError(spec007Message(err, "AI 분석 요청에 실패했습니다."));
    } finally {
      setBusyDocId(null);
    }
  }

  return (
    <section className="rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">AI 분석 상태</h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            10초마다 자동 갱신 · 전체 {rows.length}개 문서
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void reload()}
            disabled={loading}
          >
            {loading ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
          </Button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="mx-4 mt-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">
          {loading
            ? "분석 상태를 불러오는 중…"
            : "수집된 문서가 없습니다. Drive 선택 폴더에 파일을 넣으면 자동으로 분석이 시작됩니다."}
        </p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="bg-muted/50 text-xs text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">문서</th>
              <th className="px-3 py-2 font-medium">분석 상태</th>
              <th className="px-3 py-2 font-medium">시도</th>
              <th className="px-3 py-2 font-medium">최근 갱신</th>
              <th className="px-3 py-2 font-medium text-right">동작</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ document, latest, history }) => {
              const expanded = expandedDocId === document.id;
              const failureMessage = latest ? jobErrorMessage(latest) : null;
              return (
                <Fragment key={document.id}>
                  <tr
                    className="cursor-pointer border-t border-border hover:bg-muted/30"
                    onClick={() =>
                      setExpandedDocId(expanded ? null : document.id)
                    }
                  >
                    <td className="px-3 py-2">
                      <div className="font-medium">
                        {document.mirror.drive_name}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        #{document.id} · {document.mirror.drive_mime_type}
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <StatusBadge job={latest} />
                      {failureMessage &&
                        latest &&
                        ["failed", "timeout", "validation_failed"].includes(
                          latest.status,
                        ) && (
                          <div className="mt-1 text-xs text-destructive">
                            {failureMessage}
                          </div>
                        )}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {latest
                        ? `${latest.attempt_count}/${latest.max_attempts}`
                        : "—"}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {formatDateTime(latest?.updated_at ?? null)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={
                          busyDocId !== null ||
                          document.mirror.drive_state !== "active"
                        }
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleClassify(document.id);
                        }}
                      >
                        {busyDocId === document.id ? (
                          <LoaderCircle className="animate-spin" />
                        ) : (
                          <Play />
                        )}
                        {latest ? "재분석" : "분석 요청"}
                      </Button>
                    </td>
                  </tr>
                  {expanded && (
                    <tr className="border-t border-border bg-muted/20">
                      <td colSpan={5} className="px-3 py-2">
                        {history.length === 0 ? (
                          <span className="text-xs text-muted-foreground">
                            분석 이력이 없습니다.
                          </span>
                        ) : (
                          <ul className="flex flex-col gap-1">
                            {history.map((job) => (
                              <li
                                key={job.id}
                                className="flex items-center gap-3 text-xs text-muted-foreground"
                              >
                                <span className="font-mono">job#{job.id}</span>
                                <span>
                                  {job.job_type === "stale_reanalysis"
                                    ? "재분석"
                                    : "분류"}
                                </span>
                                <StatusBadge job={job} />
                                {job.candidate_id !== null && (
                                  <span>candidate#{job.candidate_id}</span>
                                )}
                                {job.last_error_code && (
                                  <span className="font-mono">
                                    {job.last_error_code}
                                  </span>
                                )}
                                <span>{formatDateTime(job.updated_at)}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
