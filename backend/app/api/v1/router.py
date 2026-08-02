"""版本 1 API 路由聚合。"""

from fastapi import APIRouter

from app.api.v1.agent_runs import router as agent_runs_router
from app.api.v1.approvals import router as approvals_router
from app.api.v1.audit_logs import router as audit_logs_router
from app.api.v1.auth import router as auth_router
from app.api.v1.calendar import router as calendar_router
from app.api.v1.emails import router as emails_router
from app.api.v1.health import router as health_router
from app.api.v1.memories import router as memories_router
from app.api.v1.users import router as users_router

router = APIRouter()
router.include_router(health_router)
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(emails_router)
router.include_router(calendar_router)
router.include_router(approvals_router)
router.include_router(agent_runs_router)
router.include_router(memories_router)
router.include_router(audit_logs_router)
