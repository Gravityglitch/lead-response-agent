"""Outbound reply adapters. The approval queue is the only caller of send()."""

from __future__ import annotations

from typing import Any, Protocol

import httpx
import structlog

from agent.config import Settings
from agent.models import Channel

log = structlog.get_logger(__name__)

SendResult = dict[str, Any]


class OutboundSender(Protocol):
    def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> SendResult:
        """Deliver a message. Returns provider metadata (message id, status)."""
        ...


class DryRunSender:
    """Default adapter: records what would have been sent. Safe for local, tests, and staging."""

    def __init__(self) -> None:
        self.sent: list[SendResult] = []

    def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> SendResult:
        record: SendResult = {
            "provider": "dry_run",
            "channel": channel,
            "to": to,
            "subject": subject,
            "body": body,
            "status": "dry_run",
        }
        self.sent.append(record)
        log.info("outbound.dry_run", channel=channel, to=to, chars=len(body))
        return record


class TwilioSender:
    """Twilio Messages API (SMS). https://www.twilio.com/docs/sms/api/message-resource"""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.account_sid = account_sid
        self.auth = (account_sid, auth_token)
        self.from_number = from_number
        self.client = client or httpx.Client(timeout=15.0)

    def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> SendResult:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        resp = self.client.post(
            url, auth=self.auth, data={"From": self.from_number, "To": to, "Body": body}
        )
        resp.raise_for_status()
        data = resp.json()
        return {"provider": "twilio", "message_id": data.get("sid"), "status": data.get("status")}


class SendGridSender:
    """SendGrid Mail Send v3 (email). https://docs.sendgrid.com/api-reference/mail-send"""

    def __init__(self, api_key: str, from_email: str, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.from_email = from_email
        self.client = client or httpx.Client(timeout=15.0)

    def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> SendResult:
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self.from_email},
            "subject": subject or "Re: your inquiry",
            "content": [{"type": "text/plain", "value": body}],
        }
        resp = self.client.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        return {
            "provider": "sendgrid",
            "message_id": resp.headers.get("X-Message-Id"),
            "status": str(resp.status_code),
        }


class ChannelRouter:
    """Picks the adapter by channel. web_form replies go out as email."""

    def __init__(self, sms: OutboundSender, email: OutboundSender) -> None:
        self.sms = sms
        self.email = email

    def send(self, channel: Channel, to: str, body: str, subject: str | None = None) -> SendResult:
        sender = self.sms if channel == "sms" else self.email
        return sender.send(channel, to, body, subject)


def build_sender(settings: Settings) -> OutboundSender:
    if settings.outbound_mode != "live":
        return DryRunSender()
    assert settings.twilio_account_sid and settings.twilio_auth_token
    assert settings.twilio_from_number
    assert settings.sendgrid_api_key and settings.sendgrid_from_email
    return ChannelRouter(
        sms=TwilioSender(
            settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number
        ),
        email=SendGridSender(settings.sendgrid_api_key, settings.sendgrid_from_email),
    )
