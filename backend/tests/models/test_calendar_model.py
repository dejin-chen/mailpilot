"""日历事件 ORM 模型测试。"""

from app.models import CalendarEvent, CalendarEventStatus


def test_calendar_event_table_contract() -> None:
    assert set(CalendarEvent.__table__.columns.keys()) == {
        "id",
        "user_id",
        "provider",
        "external_id",
        "title",
        "description",
        "start_at",
        "end_at",
        "timezone",
        "attendees",
        "location",
        "is_all_day",
        "status",
        "idempotency_key",
        "created_at",
        "updated_at",
    }
    assert {constraint.name for constraint in CalendarEvent.__table__.constraints} == {
        "pk_calendar_events",
        "fk_calendar_events_user_id_users",
        "uq_calendar_events_user_provider_external",
        "uq_calendar_events_user_idempotency_key",
        "ck_calendar_events_valid_time_range",
        "ck_calendar_events_calendar_event_status",
    }
    assert {index.name for index in CalendarEvent.__table__.indexes} == {
        "ix_calendar_events_user_status",
        "ix_calendar_events_user_time",
    }


def test_calendar_status_values_match_database_contract() -> None:
    assert [status.value for status in CalendarEventStatus] == [
        "tentative",
        "confirmed",
        "cancelled",
    ]
