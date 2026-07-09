// 로그인 (SPEC-001 User & RBAC — Login Boundary). 시안: 21-html/login-rbac.html
// JWT access token(메모리) + refresh token(httpOnly cookie). 배선은 lib/auth/session.ts.
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Building2,
  Info,
  LogIn,
  TriangleAlert,
  LoaderCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { AuthError, login } from "@/lib/auth/session";

type BadgeTone = "info" | "dark" | "success" | "warn" | "danger";

const badgeToneClass: Record<BadgeTone, string> = {
  info: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200",
  dark: "bg-foreground text-background",
  success:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  warn: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  danger: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
};

function Badge({
  tone = "info",
  children,
}: {
  tone?: BadgeTone;
  children: React.ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${badgeToneClass[tone]}`}
    >
      {children}
    </span>
  );
}

const NAV = [
  { label: "문서", href: "/documents" },
  { label: "승인", href: "/admin/approvals" },
  { label: "관리", href: "/admin/catalog" },
  { label: "로그인/RBAC", href: "/login", active: true },
];

// 시안 우측: 역할별 세션 노출 차이 (SPEC-001 Visibility Contract)
const MEMBER_ROWS: [string, string][] = [
  ["탐색 메뉴", "문서, 상세, 관계"],
  ["Admin 메뉴", "렌더하지 않음"],
  ["읽기 판정", "role/department/position policy"],
  ["민감 문서", "policy match 시만 노출"],
  ["권한 없는 문서", "목록/검색/관계에서 제거"],
];
const ADMIN_ROWS: [string, string][] = [
  ["탐색 메뉴", "모든 문서 포함"],
  ["Admin 메뉴", "승인 게이트, AI Queue"],
  ["민감 preset", "검토/수정/승인"],
  ["문서종류", "전사 catalog 추가"],
  ["RBAC 보정", "매핑 누락 사용자 확인"],
];
const STATUS_ROWS: {
  tone: BadgeTone;
  state: string;
  handling: string;
  example: string;
}[] = [
  {
    tone: "success",
    state: "active + mapped",
    handling: "정상 탐색 허용",
    example: "FE 팀 문서 18건",
  },
  {
    tone: "warn",
    state: "department_node_id 없음",
    handling: "일반 문서 탐색 제한, admin 보정 필요",
    example: "조직 매핑 필요 배너",
  },
  {
    tone: "danger",
    state: "active=false",
    handling: "문서 탐색 불가",
    example: "계정 상태 확인 필요",
  },
  {
    tone: "danger",
    state: "resigned_at 있음",
    handling: "문서 탐색 불가",
    example: "로그인 후 접근 차단",
  },
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("admin@medisolve.internal");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLogin() {
    setError(null);
    setBusy(true);
    try {
      await login({ email, password });
      router.push("/documents");
    } catch (err) {
      setError(
        err instanceof AuthError
          ? err.message
          : "로그인 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
      );
    } finally {
      setBusy(false);
    }
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
        <Badge tone="info">Google social login 없음</Badge>
      </header>

      <main className="mx-auto grid w-full max-w-6xl flex-1 gap-6 p-6 lg:grid-cols-[minmax(0,380px)_1fr]">
        {/* ── 로그인 카드 (좌) ── */}
        <section className="flex flex-col gap-4">
          <div>
            <div className="text-xs text-muted-foreground">/login</div>
            <h1 className="text-xl font-semibold">Mediness 계정 진입</h1>
          </div>

          <div className="rounded-xl border border-border bg-card">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold">로그인</h2>
              <Badge tone="dark">product users</Badge>
            </div>
            <div className="p-4">
              <div className="grid grid-cols-2 gap-3">
                <label className="col-span-2 flex flex-col gap-1 text-sm">
                  이메일
                  <input
                    type="email"
                    className="h-9 rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    disabled={busy}
                  />
                </label>
                <label className="col-span-2 flex flex-col gap-1 text-sm">
                  비밀번호
                  <input
                    type="password"
                    className="h-9 rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !busy) void handleLogin();
                    }}
                    disabled={busy}
                    autoComplete="current-password"
                  />
                </label>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                role/소속은 seed된 계정 기준으로 서버가 판정한다. 데모 비밀번호는
                seed 시 공통 부여(SEED_DEFAULT_PASSWORD).
              </p>

              {error && (
                <div
                  role="alert"
                  className="mt-3 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                >
                  <TriangleAlert className="mt-0.5 size-4" />
                  <span>{error}</span>
                </div>
              )}

              <div className="mt-3 flex gap-2">
                <Button
                  onClick={() => void handleLogin()}
                  disabled={busy || !email || !password}
                  aria-busy={busy}
                >
                  {busy ? <LoaderCircle className="animate-spin" /> : <LogIn />}
                  로그인
                </Button>
              </div>
            </div>
          </div>

          <div className="flex items-start gap-2 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 dark:border-sky-900 dark:bg-sky-950/40">
            <Info className="mt-0.5 size-4 text-sky-700 dark:text-sky-300" />
            <div>
              <strong className="text-sm">Drive OAuth는 사용자 로그인과 분리</strong>
              <p className="mt-1 text-xs text-muted-foreground">
                Drive connector refresh token, selected folder, scope는 env/admin
                설정의 영역이며 개별 사용자 Google 계정 로그인과 결합하지 않는다.
              </p>
            </div>
          </div>
        </section>

        {/* ── 역할별 노출 차이 (우) ── */}
        <section className="flex flex-col gap-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs text-muted-foreground">
                SPEC-001 User &amp; RBAC
              </div>
              <h1 className="text-xl font-semibold">역할별 노출 차이</h1>
            </div>
            <Badge tone="warn">권한 없는 문서는 보이지 않음</Badge>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <SessionPanel
              title="Member session"
              badge={<Badge tone="success">active</Badge>}
              rows={MEMBER_ROWS}
            />
            <SessionPanel
              title="Admin session"
              badge={<Badge tone="dark">all readable</Badge>}
              rows={ADMIN_ROWS}
            />
          </div>

          <div className="overflow-hidden rounded-xl border border-border">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/50 text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">사용자 상태</th>
                  <th className="px-3 py-2 font-medium">프론트 처리</th>
                  <th className="px-3 py-2 font-medium">표시 예</th>
                </tr>
              </thead>
              <tbody>
                {STATUS_ROWS.map((row) => (
                  <tr key={row.state} className="border-t border-border">
                    <td className="px-3 py-2">
                      <Badge tone={row.tone}>{row.state}</Badge>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {row.handling}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {row.example}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}

function SessionPanel({
  title,
  badge,
  rows,
}: {
  title: string;
  badge: React.ReactNode;
  rows: [string, string][];
}) {
  return (
    <div className="rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Building2 className="size-4 text-muted-foreground" />
          {title}
        </h2>
        {badge}
      </div>
      <dl className="divide-y divide-border">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between px-4 py-2">
            <dt className="text-sm text-muted-foreground">{k}</dt>
            <dd className="text-sm font-medium">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
