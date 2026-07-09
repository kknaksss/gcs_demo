// access token 메모리 보관 (ARCH-001 Accepted Defaults: refresh token은 httpOnly cookie,
// access token은 저장소에 남기지 않고 메모리에만 둔다). 새로고침 시 /auth/refresh 로 복구한다.

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function clearAccessToken(): void {
  accessToken = null;
}
