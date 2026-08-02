"""外部观察数据脱敏测试。"""

from app.observability.masking import mask_sensitive_data, summarize_value
from pydantic import SecretStr


def test_mask_sensitive_data_recursively_redacts_secrets_and_email() -> None:
    masked = mask_sensitive_data(
        {
            "authorization": "Bearer real-token",
            "nested": {
                "api_key": SecretStr("sk-real"),
                "contact": "alice@example.com",
                "message": "Authorization: Bearer abc.def.ghi",
            },
        }
    )

    assert masked["authorization"] == "[已脱敏]"  # type: ignore[index]
    nested = masked["nested"]  # type: ignore[index]
    assert nested["api_key"] == "[已脱敏]"  # type: ignore[index]
    assert nested["contact"] == "[邮箱已脱敏]"  # type: ignore[index]
    assert "abc.def.ghi" not in nested["message"]  # type: ignore[index]


def test_summarize_value_keeps_shape_without_business_content() -> None:
    summary = summarize_value(
        {"subject": "客户机密主题", "body": "客户机密正文"}
    )

    assert summary == {"type": "mapping", "keys": ["body", "subject"]}
    assert "客户机密" not in str(summary)
