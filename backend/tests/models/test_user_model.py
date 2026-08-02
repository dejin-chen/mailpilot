"""用户 ORM 模型测试。"""

from app.models import User, UserRole


def test_user_table_has_expected_columns_and_constraints() -> None:
    assert set(User.__table__.columns.keys()) == {
        "id",
        "email",
        "password_hash",
        "full_name",
        "role",
        "is_active",
        "timezone",
        "created_at",
        "updated_at",
    }
    assert {constraint.name for constraint in User.__table__.constraints} == {
        "pk_users",
        "uq_users_email",
        "ck_users_email_lowercase",
        "ck_users_user_role",
    }


def test_user_role_values_match_database_contract() -> None:
    assert [role.value for role in UserRole] == ["user", "admin"]
    assert User.__table__.c.role.type.enums == ["user", "admin"]
