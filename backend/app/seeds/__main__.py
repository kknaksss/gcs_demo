"""Seed CLI — WORK-001 Phase 3.

    uv run python -m app.seeds            # 기본 조직도 + Mediness user seed (멱등)
    uv run python -m app.seeds --force    # prod 가드 무시

ARCH-001 Accepted Defaults: seed 실행 방식은 CLI command. 재실행해도 `source_user_id`
기준 멱등 upsert이라 row 수가 늘지 않는다. prod에서 임의 실행되지 않도록 APP_ENV=prod
면 --force 없이는 거부한다 (Pre-deploy Check).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.seeds.users import seed_users


async def _run() -> None:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        result = await seed_users(session, settings.demo_user_password)
        await session.commit()
    # 데모 password 자체는 log에 남기지 않는다(Mediness credential/데모 secret 비노출).
    print(
        f"seed done: total={result.total} created={result.created} "
        f"updated={result.updated} mapped={result.mapped} "
        f"unmapped={result.unmapped}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.seeds")
    parser.add_argument(
        "--force", action="store_true", help="APP_ENV=prod에서도 강제 실행"
    )
    args = parser.parse_args()

    if get_settings().app_env == "prod" and not args.force:
        print("refusing to seed in prod without --force", file=sys.stderr)
        raise SystemExit(2)

    asyncio.run(_run())


if __name__ == "__main__":
    main()
