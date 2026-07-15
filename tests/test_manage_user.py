"""Tests for secure database user provisioning."""

from __future__ import annotations

import pytest

from scripts.manage_user import validate_password


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "alllowercase1234!",
        "ALLUPPERCASE1234!",
        "NoDigitsInThisOne!",
        "NoSymbolsInThis123",
    ],
)
def test_password_policy_rejects_weak_passwords(password: str):
    with pytest.raises(ValueError):
        validate_password(password)


def test_password_policy_accepts_strong_password():
    validate_password("Industrial-VPP-2026!")
