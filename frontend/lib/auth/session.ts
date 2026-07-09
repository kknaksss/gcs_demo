// ─────────────────────────────────────────────────────────────────────────────
// Auth 계약 격리 지점 (SPEC-001 Login Boundary / ARCH-001 Accepted Defaults)
//
// ⚠️ BE 계약 대기: 쿠키 이름·로그인 요청/응답 shape·데모 credential 정책은
//    profile-be(PLAN-008-T-001)가 확정한다. 계약이 확정되면 **이 파일만** 고치면 되도록
//    엔드포인트 경로 / 요청 body / 응답 타입 / 에러 파싱을 여기에 모아 둔다.
//    화면(app/login/page.tsx)과 api client(lib/api/client.ts)는 이 모듈의 함수/타입만 의존한다.
// ─────────────────────────────────────────────────────────────────────────────

import { API_BASE_URL } from "@/lib/api/config";
import { clearAccessToken, setAccessToken } from "@/lib/api/token";

/** 현재 로그인 사용자 (SPEC-001 Product User Model 일부 — 표시/권한 판정용). */
export interface AuthUser {
  id: number;
  email: string;
  name: string;
  role: string;
  department_node_id: number | null;
}

/**
 * 로그인 요청 body — 계약 확정(WORK-001 Phase 2):
 * email + password 인증. 데모 비밀번호는 seed 시 공통 부여(SEED_DEFAULT_PASSWORD, 기본 demo1234!).
 * role/소속은 클라이언트가 고르지 않고 seed된 계정 기준으로 서버가 판정한다.
 * refresh token은 httpOnly cookie `refresh_token` (SameSite=Lax, Path=/auth).
 */
export interface LoginInput {
  email: string;
  password: string;
}

interface LoginResponseBody {
  access_token: string;
  user: AuthUser;
}

/** 사용자에게 그대로 보여줄 수 있는 로그인 에러. */
export class AuthError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "AuthError";
  }
}

function buildLoginBody(input: LoginInput): Record<string, unknown> {
  return {
    email: input.email,
    password: input.password,
  };
}

// BE detail shape: { error_code, message(영문) }. 한국어 카피는 FE가 error_code로 매핑한다 (컨벤션).
const AUTH_ERROR_MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: "이메일 또는 비밀번호가 올바르지 않습니다.",
  ACCOUNT_DISABLED: "비활성 또는 퇴사 처리된 계정입니다.",
  UNAUTHENTICATED: "세션이 없습니다. 다시 로그인해주세요.",
  INVALID_REFRESH: "세션이 만료되었습니다. 다시 로그인해주세요.",
};

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as {
      detail?: string | { error_code?: string; message?: string };
    };
    if (typeof body.detail === "string" && body.detail) return body.detail;
    if (body.detail && typeof body.detail === "object") {
      const mapped = body.detail.error_code
        ? AUTH_ERROR_MESSAGES[body.detail.error_code]
        : undefined;
      if (mapped) return mapped;
    }
  } catch {
    // JSON 이 아니면 상태코드 기반 문구로 폴백
  }
  if (res.status === 401) return "이메일 또는 비밀번호가 올바르지 않습니다.";
  if (res.status === 403)
    return "계정 상태를 확인해주세요. 비활성 또는 퇴사 처리된 계정은 접근할 수 없습니다.";
  return "로그인 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요.";
}

/** 로그인. 성공 시 access token을 메모리에 저장하고 사용자 정보를 반환한다. refresh token은 httpOnly cookie로 세팅된다. */
export async function login(input: LoginInput): Promise<AuthUser> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include", // refresh token httpOnly cookie 수신
      body: JSON.stringify(buildLoginBody(input)),
    });
  } catch {
    throw new AuthError("서버에 연결할 수 없습니다. 네트워크 상태를 확인해주세요.", 0);
  }

  if (!res.ok) {
    throw new AuthError(await readError(res), res.status);
  }

  const data = (await res.json()) as LoginResponseBody;
  setAccessToken(data.access_token);
  return data.user;
}

/**
 * access token 만료(401) 시 호출. httpOnly refresh cookie로 새 access token을 받아 저장한다.
 * 자동 재시도 루프 방지를 위해 api client를 거치지 않고 raw fetch를 쓴다.
 * 성공하면 true, 실패하면 토큰을 비우고 false.
 */
export async function refreshAccessToken(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) {
      clearAccessToken();
      return false;
    }
    const data = (await res.json()) as { access_token: string };
    setAccessToken(data.access_token);
    return true;
  } catch {
    clearAccessToken();
    return false;
  }
}

/** 로그아웃. 서버 세션/refresh cookie 무효화 + 메모리 토큰 제거. */
export async function logout(): Promise<void> {
  try {
    await fetch(`${API_BASE_URL}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
  } finally {
    clearAccessToken();
  }
}
