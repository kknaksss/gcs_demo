// backend schema 기준 typed fetch wrapper (ARCH-001 §7).
// access token은 메모리(lib/api/token) 보관, refresh token은 httpOnly cookie(credentials: include).
// 401 이면 /auth/refresh 로 access token을 한 번 갱신하고 원요청을 재실행한다.
// endpoint별 응답 타입은 backend Pydantic schema를 기준으로 work에서 채운다.

import { refreshAccessToken } from "@/lib/auth/session";
import { API_BASE_URL } from "./config";
import { getAccessToken } from "./token";

/** BE 에러봉투 {detail:{error_code,message}} 를 담는 API 에러 (SPEC-002 Case Matrix 등). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly errorCode: string | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function toApiError(res: Response, path: string): Promise<ApiError> {
  try {
    const body = (await res.json()) as {
      detail?: { error_code?: string; message?: string } | string;
    };
    if (typeof body.detail === "object" && body.detail !== null) {
      return new ApiError(
        res.status,
        body.detail.error_code ?? null,
        body.detail.message ?? `API ${res.status}: ${path}`,
      );
    }
    if (typeof body.detail === "string") {
      return new ApiError(res.status, null, body.detail);
    }
  } catch {
    // JSON 아님 — 상태코드 기반 메시지로 폴백
  }
  return new ApiError(res.status, null, `API ${res.status}: ${path}`);
}

function request(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    ...init,
    headers,
  });
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res = await request(path, init);

  // access token 만료 시 1회 갱신 후 재시도
  if (res.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      res = await request(path, init);
    }
  }

  if (!res.ok) {
    throw await toApiError(res, path);
  }
  return res.json() as Promise<T>;
}
