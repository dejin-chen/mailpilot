"""审批反馈区分一次性修改与长期记忆更新的节点测试。"""

from uuid import UUID, uuid4

import pytest
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.memory_feedback import (
    ApplyApprovalFeedbackNode,
    has_explicit_long_term_memory_signal,
)
from app.agent.schemas import ModelUsage
from app.agent.state import MailAgentState, create_initial_state
from app.integrations.llm.client import StructuredLlmResult
from app.models.memory import MemoryType
from app.schemas.memory_feedback import (
    EmailStyleMemoryPatch,
    EmailStyleMemoryUpdateProposal,
    FeedbackMemoryDecision,
    MemoryFeedbackUpdateResult,
)
from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel


class FakeFeedbackLlm:
    def __init__(self, decision: FeedbackMemoryDecision) -> None:
        self.decision = decision
        self.calls: list[str] = []

    async def ainvoke_structured[SchemaT: BaseModel](
        self,
        *,
        operation: str,
        messages: list[BaseMessage],
        schema: type[SchemaT],
    ) -> StructuredLlmResult[SchemaT]:
        assert messages
        self.calls.append(operation)
        return StructuredLlmResult(
            parsed=schema.model_validate(self.decision.model_dump(mode="json")),
            usage=ModelUsage(
                operation=operation,
                model_name="fake-model",
                input_tokens=8,
                output_tokens=4,
                total_tokens=12,
                latency_ms=1,
            ),
        )


class FakeMemoryUpdater:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def apply(self, **kwargs: object) -> MemoryFeedbackUpdateResult:
        self.calls.append(kwargs)
        proposal = kwargs["proposal"]
        assert isinstance(proposal, EmailStyleMemoryUpdateProposal)
        return MemoryFeedbackUpdateResult(
            applied=True,
            reused=False,
            memory_id=uuid4(),
            memory_type=proposal.memory_type,
            memory_key=proposal.memory_key,
            version=2,
        )


def _context(user_id: UUID | None = None) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=user_id or uuid4(),
        agent_run_id=uuid4(),
        request_id="memory-feedback-node-test",
        user_timezone="Asia/Shanghai",
    )


def _state(feedback: str) -> MailAgentState:
    state = create_initial_state(email_thread_id=uuid4())
    state.update(
        {
            "sender": "manager@example.com",
            "regeneration_feedback": feedback,
            "approval_request_id": uuid4(),
        }
    )
    return state


def _email_style_decision(*, evidence: str) -> FeedbackMemoryDecision:
    return FeedbackMemoryDecision(
        should_update=True,
        proposal=EmailStyleMemoryUpdateProposal(
            memory_type=MemoryType.EMAIL_STYLE,
            patch=EmailStyleMemoryPatch(tone="简洁专业"),
            evidence=evidence,
            reason="用户明确要求今后保持这种风格",
        ),
        reason="属于稳定邮件风格偏好",
        confidence=0.98,
    )


@pytest.mark.parametrize(
    ("feedback", "expected"),
    [
        ("这次语气再简洁一点", False),
        ("这封邮件改正式些", False),
        ("以后邮件都简洁一点", True),
        ("请记住这个偏好：默认使用专业语气", True),
    ],
)
def test_long_term_signal_is_conservative(feedback: str, expected: bool) -> None:
    assert has_explicit_long_term_memory_signal(feedback) is expected


@pytest.mark.asyncio
async def test_one_time_feedback_regenerates_without_calling_memory_llm() -> None:
    llm = FakeFeedbackLlm(_email_style_decision(evidence="简洁"))
    updater = FakeMemoryUpdater()

    update = await ApplyApprovalFeedbackNode(llm, updater)(
        _state("这次语气再简洁一点"),
        Runtime(context=_context()),
    )

    assert update["proposal_version"] == 2
    assert update["regeneration_count"] == 1
    assert "memory_update" not in update
    assert llm.calls == []
    assert updater.calls == []


@pytest.mark.asyncio
async def test_explicit_long_term_feedback_applies_structured_patch() -> None:
    feedback = "以后邮件都使用简洁专业的语气，请记住"
    llm = FakeFeedbackLlm(_email_style_decision(evidence="以后邮件都使用简洁专业的语气"))
    updater = FakeMemoryUpdater()
    state = _state(feedback)
    context = _context()

    update = await ApplyApprovalFeedbackNode(llm, updater)(
        state,
        Runtime(context=context),
    )

    assert llm.calls == ["analyze_memory_feedback"]
    assert len(updater.calls) == 1
    assert updater.calls[0]["user_id"] == context.user_id
    assert updater.calls[0]["approval_request_id"] == state["approval_request_id"]
    assert update["memory_update"].version == 2  # type: ignore[union-attr]
    assert update["model_usages"][0].total_tokens == 12  # type: ignore[index,union-attr]


@pytest.mark.asyncio
async def test_model_cannot_invent_evidence_for_memory_update() -> None:
    llm = FakeFeedbackLlm(_email_style_decision(evidence="用户没有说过的证据"))
    updater = FakeMemoryUpdater()

    update = await ApplyApprovalFeedbackNode(llm, updater)(
        _state("以后邮件都简洁一点"),
        Runtime(context=_context()),
    )

    assert update["run_status"].value == "failed"  # type: ignore[union-attr]
    assert update["errors"][0].code == "MEMORY_FEEDBACK_POLICY_VIOLATION"  # type: ignore[index,union-attr]
    assert updater.calls == []
