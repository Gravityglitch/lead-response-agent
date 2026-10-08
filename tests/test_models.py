import pytest
from pydantic import ValidationError

from agent.models import Category, Classification, Inquiry


def test_inquiry_accepts_webhook_shape() -> None:
    inq = Inquiry.model_validate({"channel": "sms", "from": "+15550100000", "body": "hi"})
    assert inq.sender == "+15550100000"
    assert inq.model_dump(by_alias=True)["from"] == "+15550100000"


def test_inquiry_rejects_unknown_channel_and_empty_body() -> None:
    with pytest.raises(ValidationError):
        Inquiry.model_validate({"channel": "fax", "from": "x", "body": "hi"})
    with pytest.raises(ValidationError):
        Inquiry.model_validate({"channel": "sms", "from": "x", "body": ""})


def test_classification_has_exactly_five_categories() -> None:
    assert {c.value for c in Category} == {
        "availability",
        "pricing",
        "showing_request",
        "maintenance",
        "other",
    }
    with pytest.raises(ValidationError):
        Classification(category="spam", confidence=0.9, rationale="x")
