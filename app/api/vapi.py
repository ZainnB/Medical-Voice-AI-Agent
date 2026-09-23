"""Vapi webhooks: tool calls during a phone call, and the end-of-call report afterwards.

Payloads are parsed defensively because Vapi's tool-call shape varies across versions:
  {id, name, arguments|parameters}                        (flat, as in the server-events docs)
  {id, type: "function", function: {name, arguments}}     (OpenAI style, as in the SDK types)
  message.toolWithToolCallList[].toolCall                 (when toolCallList is absent)
and `arguments` may be an object or a JSON string.

/vapi/tools must always answer HTTP 200 with a single-line string result per tool call;
Vapi ignores the body of any other status, and the LLM needs to read the error.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db import get_db
from app.security import require_vapi_secret
from app.services import calls as call_service
from app.services.voice_tools import SYSTEM_ERROR, CallContext, run_tool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vapi", tags=["vapi webhooks"], dependencies=[Depends(require_vapi_secret)])


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None when the arguments could not be parsed


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_arguments(raw: Any) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, dict) else None


def parse_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    """Extract (id, name, arguments) from any supported tool-call payload shape."""
    items = message.get("toolCallList")
    if not isinstance(items, list) or not items:
        items = []
        for entry in message.get("toolWithToolCallList") or []:
            tool_call = _dict(_dict(entry).get("toolCall"))
            if tool_call:
                items.append({"name": _dict(entry).get("name")} | {k: v for k, v in tool_call.items() if v is not None})
    tool_calls = []
    for item in items:
        if not isinstance(item, dict):
            continue
        function = _dict(item.get("function"))
        raw_arguments = next(
            (
                value
                for value in (item.get("arguments"), item.get("parameters"),
                              function.get("arguments"), function.get("parameters"))
                if value is not None
            ),
            None,
        )
        tool_calls.append(
            ToolCall(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or function.get("name") or ""),
                arguments=_parse_arguments(raw_arguments),
            )
        )
    return tool_calls


def call_context(message: dict[str, Any]) -> CallContext:
    """Vapi call ID and caller-ID number (either may be missing, e.g. dashboard tool tests)."""
    call = _dict(message.get("call"))
    customer = _dict(call.get("customer")) or _dict(message.get("customer"))
    return CallContext(call_id=call.get("id") or None, caller_number=customer.get("number") or None)


def _compact(result: dict[str, Any]) -> str:
    """Single-line JSON string (Vapi requires single-line tool results)."""
    return json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)


def handle_tool_calls(db: Session, message: dict[str, Any]) -> dict[str, Any]:
    """Run every tool call in the message and build Vapi's {"results": [...]} response."""
    context = call_context(message)
    results = []
    for tool_call in parse_tool_calls(message):
        if tool_call.arguments is None:
            result: dict[str, Any] = {"ok": False, "message": "Tool arguments were not valid JSON; please retry."}
        else:
            try:
                result = run_tool(db, tool_call.name, tool_call.arguments, context)
            except Exception:  # run_tool already guards; this is a last line of defence
                logger.exception("tool_crashed", extra={"fields": {"tool": tool_call.name}})
                result = SYSTEM_ERROR
        logger.info(
            "tool_call",
            extra={
                "fields": {
                    "tool": tool_call.name,
                    "tool_call_id": tool_call.id,
                    "call_id": context.call_id,
                    "arguments": tool_call.arguments,
                    "ok": result.get("ok", result.get("found")),
                    "result": result,
                }
            },
        )
        results.append({"name": tool_call.name, "toolCallId": tool_call.id, "result": _compact(result)})
    return {"results": results}


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _transcript(artifact: dict[str, Any]) -> str | None:
    """Use Vapi's transcript text, or rebuild it from artifact.messages."""
    if isinstance(artifact.get("transcript"), str) and artifact["transcript"].strip():
        return artifact["transcript"]
    speakers = {"assistant": "AI", "bot": "AI", "user": "User"}
    lines = [
        f"{speakers[m['role']]}: {m.get('message') or m.get('content') or ''}".rstrip()
        for m in artifact.get("messages") or []
        if isinstance(m, dict) and m.get("role") in speakers
    ]
    return "\n".join(lines) or None


def handle_end_of_call(db: Session, message: dict[str, Any]) -> None:
    """Persist the end-of-call report and log the final collected payload for every call."""
    context = call_context(message)
    if not context.call_id:
        logger.warning("end_of_call_without_call_id")
        return
    call = _dict(message.get("call"))
    analysis = _dict(message.get("analysis")) or _dict(call.get("analysis"))
    call_log = call_service.record_end_of_call(
        db,
        vapi_call_id=context.call_id,
        caller_number=context.caller_number,
        ended_reason=message.get("endedReason") or call.get("endedReason"),
        summary=analysis.get("summary"),
        transcript=_transcript(_dict(message.get("artifact")) or _dict(call.get("artifact"))),
        started_at=_parse_timestamp(message.get("startedAt") or call.get("startedAt")),
        ended_at=_parse_timestamp(message.get("endedAt") or call.get("endedAt")),
    )
    fields = {
        "call_id": call_log.vapi_call_id,
        "outcome": call_log.outcome,
        "ended_reason": call_log.ended_reason,
        "patient_id": call_log.patient_id,
        "collected_data": call_log.collected_data,
    }
    if call_log.patient_id is None:
        # Caller hung up (or the save failed) before a patient was saved: keep what we have.
        logger.warning("call_ended_without_saved_patient", extra={"fields": fields})
    else:
        logger.info("call_ended", extra={"fields": fields})


async def _message(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        logger.warning("vapi_webhook_invalid_json")
        return {}
    return _dict(_dict(body).get("message"))


def _dispatch(db: Session, message: dict[str, Any]) -> dict[str, Any]:
    message_type = message.get("type")
    if message_type == "tool-calls":
        return handle_tool_calls(db, message)
    if message_type == "end-of-call-report":
        handle_end_of_call(db, message)
    return {}


@router.post("/tools", summary="Vapi tool calls (always HTTP 200)")
async def vapi_tools(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Receives `tool-calls` messages; returns `{"results": [{toolCallId, result}]}`."""
    return await run_in_threadpool(_dispatch, db, await _message(request))


@router.post("/events", summary="Vapi server messages (end-of-call report)")
async def vapi_events(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Stores end-of-call reports. Other message types are acknowledged and ignored."""
    return await run_in_threadpool(_dispatch, db, await _message(request))
