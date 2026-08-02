"""MailPilot 可观测性边界。"""

from app.observability.base import Observability, Observation
from app.observability.factory import build_observability, get_observability
from app.observability.noop import NoOpObservability

__all__ = [
    "NoOpObservability",
    "Observation",
    "Observability",
    "build_observability",
    "get_observability",
]
