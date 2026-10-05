"""Persist the fixed development identity through the shared unit of work."""

from application.ports import UnitOfWorkPort, UserRepositoryPort
from application.records import DEVELOPMENT_EMAIL, DEVELOPMENT_USER_ID, DevelopmentUser
from domain.ports import ClockPort


async def ensure_development_identity(
    uow: UnitOfWorkPort, users: UserRepositoryPort, clock: ClockPort
) -> DevelopmentUser:
    candidate = DevelopmentUser(
        user_id=DEVELOPMENT_USER_ID,
        email=DEVELOPMENT_EMAIL,
        password_hash=None,
        is_development=True,
        created_at=clock.now_utc(),
    )
    async with uow.transaction() as tx:
        return await users.ensure_development(candidate, tx)
