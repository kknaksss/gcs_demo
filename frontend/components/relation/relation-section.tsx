// 문서 연결 — relation 후보 섹션 (SPEC-005 U-6/S-5, DEC-021). WORK-005 Phase 3.
// resolved/unresolved 표시, 대상 선택(문서 검색)/보류/제거/재매칭.
// unresolved는 승인 전 graph에 반영되지 않고, 새 document row는 만들지 않는다.
"use client";

import { useState } from "react";
import { LoaderCircle, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { searchDocuments } from "@/lib/api/documents";
import {
  holdRelationCandidate,
  rematchRelationCandidate,
  removeRelationCandidate,
  resolveRelationCandidate,
  spec005Message,
  type RelationCandidateItem,
  type RelationCandidateState,
} from "@/lib/api/approvals";

const STATE_BADGE: Record<RelationCandidateState, string> = {
  pending:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  unresolved: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  approved:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  removed: "bg-muted text-muted-foreground",
};

// 원장 state → 표시 라벨 (target 있는 pending은 resolved로 표기 — U-6)
function stateLabel(rel: RelationCandidateItem): string {
  if (rel.state === "pending") return "resolved";
  return rel.state;
}

interface DocumentHit {
  id: number;
  drive_name: string;
}

interface RelationSectionProps {
  relations: RelationCandidateItem[];
  sourceDocumentId: number;
  disabled: boolean;
  onChanged: () => void;
}

export function RelationSection({
  relations,
  sourceDocumentId,
  disabled,
  onChanged,
}: RelationSectionProps) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 대상 선택 picker 상태 (relation candidate id 기준)
  const [pickerId, setPickerId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<DocumentHit[]>([]);
  const [searching, setSearching] = useState(false);
  // 재매칭 제안 결과
  const [suggestion, setSuggestion] = useState<{
    relationId: number;
    targetId: number | null;
    targetName: string | null;
  } | null>(null);

  async function run(relationId: number, action: () => Promise<unknown>) {
    setBusyId(relationId);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(spec005Message(err, "relation 후보 처리에 실패했습니다."));
    } finally {
      setBusyId(null);
    }
  }

  async function handleSearch() {
    // WORK-006: /admin/documents 100건 클라이언트 필터 → /search/documents 교체.
    // 검색 결과는 승인(approved)·active 문서만 온다 — SPEC-006 Validation
    // (relation target은 승인된 document id) 기준과 일치한다.
    setSearching(true);
    setError(null);
    try {
      const res = await searchDocuments(query.trim(), { limit: 50 });
      setHits(
        res.results
          .filter((d) => d.document_id !== sourceDocumentId)
          .map((d) => ({ id: d.document_id, drive_name: d.drive_name })),
      );
    } catch (err) {
      setError(spec005Message(err, "문서 검색에 실패했습니다."));
    } finally {
      setSearching(false);
    }
  }

  async function handleRematch(relationId: number) {
    setBusyId(relationId);
    setError(null);
    setSuggestion(null);
    try {
      const res = await rematchRelationCandidate(relationId);
      setSuggestion({
        relationId,
        targetId: res.suggested_target_document_id,
        targetName: res.suggested_target_drive_name,
      });
    } catch (err) {
      setError(spec005Message(err, "재매칭에 실패했습니다."));
    } finally {
      setBusyId(null);
    }
  }

  const open = relations.filter((r) => r.state !== "removed");

  return (
    <section className="rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">문서 연결 — relation 후보</h2>
        <span className="inline-flex items-center rounded-full bg-sky-100 px-2.5 py-0.5 text-xs font-medium text-sky-800 dark:bg-sky-950 dark:text-sky-200">
          승인 전 graph 제외
        </span>
      </div>

      {error && (
        <div
          role="alert"
          className="mx-4 mt-3 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      {open.length === 0 ? (
        <p className="px-4 py-5 text-sm text-muted-foreground">
          relation 후보가 없습니다.
        </p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="bg-muted/50 text-xs text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">후보</th>
              <th className="px-3 py-2 font-medium">상태</th>
              <th className="px-3 py-2 font-medium">처리</th>
            </tr>
          </thead>
          <tbody>
            {open.map((rel) => {
              const busy = busyId === rel.id;
              const actionable =
                !disabled &&
                (rel.state === "pending" || rel.state === "unresolved");
              return (
                <tr key={rel.id} className="border-t border-border align-top">
                  <td className="px-3 py-2">
                    <div className="font-mono text-xs">{rel.raw_label}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {rel.suggested_relation_type}
                      {rel.target_drive_name
                        ? ` · 대상: ${rel.target_drive_name}`
                        : null}
                    </div>
                    {rel.state === "unresolved" && (
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        대상 문서를 찾을 수 없습니다.
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATE_BADGE[rel.state]}`}
                    >
                      {stateLabel(rel)}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {rel.state === "approved" ? (
                      <span className="text-xs text-muted-foreground">
                        확정 graph 반영됨
                      </span>
                    ) : actionable ? (
                      <div className="flex flex-col gap-2">
                        <div className="flex flex-wrap gap-1.5">
                          <Button
                            size="xs"
                            variant="outline"
                            disabled={busy}
                            onClick={() => {
                              setPickerId(pickerId === rel.id ? null : rel.id);
                              setQuery("");
                              setHits([]);
                            }}
                          >
                            대상 선택
                          </Button>
                          <Button
                            size="xs"
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              void run(rel.id, () => holdRelationCandidate(rel.id))
                            }
                          >
                            보류
                          </Button>
                          <Button
                            size="xs"
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              void run(rel.id, () =>
                                removeRelationCandidate(rel.id),
                              )
                            }
                          >
                            제거
                          </Button>
                          <Button
                            size="xs"
                            variant="outline"
                            disabled={busy}
                            onClick={() => void handleRematch(rel.id)}
                          >
                            {busy && <LoaderCircle className="animate-spin" />}
                            재매칭
                          </Button>
                        </div>

                        {/* 재매칭 제안 — 확정은 target 지정으로만 (DEC-021) */}
                        {suggestion?.relationId === rel.id && (
                          <div className="rounded-lg border border-border bg-muted/30 px-2.5 py-2 text-xs">
                            {suggestion.targetId !== null ? (
                              <div className="flex items-center justify-between gap-2">
                                <span>
                                  재매칭 제안: {suggestion.targetName} (#
                                  {suggestion.targetId})
                                </span>
                                <Button
                                  size="xs"
                                  disabled={busy}
                                  onClick={() =>
                                    void run(rel.id, () =>
                                      resolveRelationCandidate(
                                        rel.id,
                                        suggestion.targetId as number,
                                      ),
                                    )
                                  }
                                >
                                  이 문서로 지정
                                </Button>
                              </div>
                            ) : (
                              <span className="text-muted-foreground">
                                재매칭 결과 없음 — 일치하는 문서를 찾지 못했습니다.
                              </span>
                            )}
                          </div>
                        )}

                        {/* 대상 선택 picker — 기존 문서 검색 */}
                        {pickerId === rel.id && (
                          <div className="rounded-lg border border-border bg-muted/30 p-2.5">
                            <div className="flex items-center gap-1.5">
                              <input
                                className="h-8 min-w-0 flex-1 rounded-lg border border-input bg-background px-2 text-xs outline-none focus-visible:border-ring"
                                value={query}
                                placeholder="문서 이름 검색"
                                onChange={(e) => setQuery(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") void handleSearch();
                                }}
                              />
                              <Button
                                size="xs"
                                variant="outline"
                                disabled={searching}
                                onClick={() => void handleSearch()}
                              >
                                {searching ? (
                                  <LoaderCircle className="animate-spin" />
                                ) : (
                                  <Search />
                                )}
                                검색
                              </Button>
                              <Button
                                size="xs"
                                variant="ghost"
                                onClick={() => setPickerId(null)}
                                aria-label="닫기"
                              >
                                <X />
                              </Button>
                            </div>
                            {hits.length > 0 && (
                              <ul className="mt-2 flex max-h-40 flex-col gap-1 overflow-y-auto">
                                {hits.map((hit) => (
                                  <li key={hit.id}>
                                    <button
                                      type="button"
                                      className="w-full rounded-md px-2 py-1 text-left text-xs hover:bg-muted"
                                      onClick={() =>
                                        void run(rel.id, async () => {
                                          await resolveRelationCandidate(
                                            rel.id,
                                            hit.id,
                                          );
                                          setPickerId(null);
                                        })
                                      }
                                    >
                                      {hit.drive_name}{" "}
                                      <span className="text-muted-foreground">
                                        #{hit.id}
                                      </span>
                                    </button>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <p className="border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
        target을 지정한 후보만 metadata 승인 시 확정 graph에 반영된다.
        unresolved 후보로 새 문서를 만들지 않는다.
      </p>
    </section>
  );
}
