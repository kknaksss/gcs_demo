// API base URL (env로 주입, 기본은 로컬 backend). docker compose 기준 backend 포트는 work에서 확정.
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
