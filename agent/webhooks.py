"""Provider webhook routes. Each is behind a feature flag and its own verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping

from fastapi import APIRouter, HTTPException, Request

from agent.models import AgentResult, Inquiry

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def twilio_signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    """Twilio request signing: HMAC-SHA1 over url + sorted (key+value) pairs, base64 encoded.

    https://www.twilio.com/docs/usage/webhooks/webhooks-security
    """
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio(auth_token: str, url: str, params: Mapping[str, str], signature: str) -> bool:
    return hmac.compare_digest(twilio_signature(auth_token, url, params), signature)


@router.post("/twilio", response_model=AgentResult)
async def twilio_inbound(request: Request) -> AgentResult:
    settings = request.app.state.settings
    if not settings.twilio_webhook_enabled:
        raise HTTPException(404, "twilio webhook disabled")
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = settings.twilio_webhook_url or str(request.url)
    signature = request.headers.get("X-Twilio-Signature", "")
    if not verify_twilio(settings.twilio_auth_token or "", url, form, signature):
        raise HTTPException(403, "invalid Twilio signature")
    body = form.get("Body", "").strip()
    if not body:
        raise HTTPException(422, "empty Body")
    inquiry = Inquiry(
        channel="sms",
        sender=form.get("From", "unknown"),
        body=body,
        provider_message_id=form.get("MessageSid") or form.get("SmsSid"),
    )
    return request.app.state.agent.handle(inquiry)


@router.post("/sendgrid", response_model=AgentResult)
async def sendgrid_inbound(request: Request) -> AgentResult:
    """SendGrid Inbound Parse posts multipart form data and does not sign it; a shared
    secret in the query string (?token=) is the gate."""
    settings = request.app.state.settings
    if not settings.sendgrid_webhook_enabled:
        raise HTTPException(404, "sendgrid webhook disabled")
    token = request.query_params.get("token", "")
    expected = settings.sendgrid_inbound_secret or ""
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(403, "invalid inbound token")
    form = {k: str(v) for k, v in (await request.form()).items()}
    body = (form.get("text") or form.get("html") or "").strip()
    if not body:
        raise HTTPException(422, "empty message body")
    message_id = _header_value(form.get("headers", ""), "Message-ID")
    inquiry = Inquiry(
        channel="email",
        sender=form.get("from", "unknown"),
        subject=form.get("subject") or None,
        body=body,
        provider_message_id=message_id,
    )
    return request.app.state.agent.handle(inquiry)


def _header_value(raw_headers: str, name: str) -> str | None:
    prefix = name.lower() + ":"
    for line in raw_headers.splitlines():
        if line.lower().startswith(prefix):
            return line.split(":", 1)[1].strip() or None
    return None
