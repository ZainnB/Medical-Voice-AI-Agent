"""Create or update the Vapi tools and the "Riley" assistant, then bind the phone number.

Idempotent: tools are matched by function name and the assistant by name, so re-running
updates them in place (e.g. after editing prompts/system_prompt.md).

    python scripts/vapi_setup.py             # apply
    python scripts/vapi_setup.py --dry-run   # print the payloads, call nothing

Reads VAPI_API_KEY, VAPI_PHONE_NUMBER_ID, PUBLIC_BASE_URL and VAPI_WEBHOOK_SECRET from the
environment or the repo's .env file. Field names were checked against docs.vapi.ai
(API reference + server-events/tool docs) on 2026-09-23.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
VAPI_API = "https://api.vapi.ai"
ASSISTANT_NAME = "Patient Intake — Riley"
FIRST_MESSAGE = (
    "Hi, thanks for calling Riverside Family Health. I'm Riley, and I can help you get registered "
    "as a new patient. Can I start with your first and last name?"
)


class SetupSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    vapi_api_key: str = Field(min_length=1)
    vapi_phone_number_id: str = Field(min_length=1)
    public_base_url: str = Field(min_length=1)
    vapi_webhook_secret: str = Field(min_length=16)  # must equal VAPI_WEBHOOK_SECRET on Railway


class SetupError(RuntimeError):
    """A Vapi API call failed."""


# ---- Tool definitions ------------------------------------------------------------------------

def _spoken(english: str, spanish: str) -> list[dict[str, str]]:
    """Per-language variants for a tool message (Vapi picks the active call language)."""
    return [
        {"type": "text", "text": english, "language": "en"},
        {"type": "text", "text": spanish, "language": "es"},
    ]


FAILED_MESSAGE = {
    "type": "request-failed",
    "contents": _spoken(
        "I'm having trouble reaching our system.", "Estoy teniendo problemas para conectarme con nuestro sistema."
    ),
}

PATIENT_PROPERTIES: dict[str, dict[str, Any]] = {
    "first_name": {"type": "string", "description": "Legal first name, spelled as confirmed with the caller"},
    "last_name": {"type": "string", "description": "Legal last name, spelled as confirmed with the caller"},
    "date_of_birth": {"type": "string", "description": "Date of birth in MM/DD/YYYY format"},
    "sex": {
        "type": "string",
        "enum": ["Male", "Female", "Other", "Decline to Answer"],
        "description": "One of: Male, Female, Other, Decline to Answer",
    },
    "phone_number": {"type": "string", "description": "10-digit US phone number, digits only, e.g. 2125550123"},
    "email": {"type": "string", "description": "Email address, e.g. jane.doe@gmail.com"},
    "address_line_1": {"type": "string", "description": "Street address, e.g. 123 Main St"},
    "address_line_2": {"type": "string", "description": "Apartment, suite, or unit, e.g. Apt 4B. Omit if none"},
    "city": {"type": "string", "description": "City name"},
    "state": {"type": "string", "description": "2-letter US state code, e.g. TX"},
    "zip_code": {"type": "string", "description": "5-digit ZIP code or ZIP+4, e.g. 78701"},
    "insurance_provider": {"type": "string", "description": "Insurance company name, e.g. Aetna"},
    "insurance_member_id": {
        "type": "string",
        "description": "Insurance member ID exactly as read back: letters, digits, and hyphens only",
    },
    "preferred_language": {"type": "string", "description": "Preferred language, e.g. English or Spanish"},
    "emergency_contact_name": {"type": "string", "description": "Emergency contact's full name"},
    "emergency_contact_phone": {"type": "string", "description": "Emergency contact's 10-digit US phone number"},
}
REQUIRED_PATIENT_FIELDS = [
    "first_name", "last_name", "date_of_birth", "sex", "phone_number", "address_line_1", "city", "state", "zip_code",
]
PATIENT_ID = {"type": "string", "description": "The patient_id returned by lookup_patient_by_phone"}


def tool_payloads(settings: SetupSettings) -> list[dict[str, Any]]:
    """The five function tools, all pointing at /vapi/tools with the shared secret."""
    server = {
        "url": f"{settings.public_base_url.rstrip('/')}/vapi/tools",
        "headers": {"X-Vapi-Secret": settings.vapi_webhook_secret},
        "timeoutSeconds": 15,
    }

    def tool(name: str, description: str, properties: dict[str, Any], required: list[str],
             start: tuple[str, str] | None) -> dict[str, Any]:
        messages = [FAILED_MESSAGE]
        if start:
            messages.insert(0, {"type": "request-start", "contents": _spoken(*start)})
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
            "messages": messages,
            "server": server,
        }

    return [
        tool(
            "lookup_patient_by_phone",
            "Find existing patient records by phone number. Returns only patient IDs and names.",
            {"phone_number": PATIENT_PROPERTIES["phone_number"]},
            ["phone_number"],
            ("Let me check our system.", "Permítame revisar nuestro sistema."),
        ),
        tool(
            "verify_patient_identity",
            "Check whether the date of birth the caller just said matches the record. Returns verified true or "
            "false. The date on file is never revealed.",
            {"patient_id": PATIENT_ID, "date_of_birth": PATIENT_PROPERTIES["date_of_birth"]},
            ["patient_id", "date_of_birth"],
            ("Thanks, let me verify that.", "Gracias, permítame verificarlo."),
        ),
        tool(
            "validate_patient",
            "Check all collected details for errors WITHOUT saving. Call this before reading the details back.",
            PATIENT_PROPERTIES,
            [],
            None,
        ),
        tool(
            "create_patient",
            "Save a new patient. Only call this after the caller has confirmed every detail.",
            PATIENT_PROPERTIES,
            REQUIRED_PATIENT_FIELDS,
            ("One moment while I save that for you.", "Un momento mientras guardo su información."),
        ),
        tool(
            "update_patient",
            "Update an existing patient whose identity was verified in this call. Include only the fields that "
            "are changing, plus patient_id and verified_date_of_birth.",
            {
                "patient_id": PATIENT_ID,
                "verified_date_of_birth": {
                    "type": "string",
                    "description": "The date of birth the caller gave when verifying identity (MM/DD/YYYY). "
                    "Used only to confirm identity; to change the date of birth, also send date_of_birth.",
                },
                **PATIENT_PROPERTIES,
            },
            ["patient_id", "verified_date_of_birth"],
            ("One moment while I update that for you.", "Un momento mientras actualizo su información."),
        ),
    ]


# ---- Assistant definition --------------------------------------------------------------------

def load_system_prompt() -> str:
    """The prompt file minus its <!-- design comments --> (they are for humans, not the model)."""
    text = (ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def assistant_payload(settings: SetupSettings, tool_ids: list[str]) -> dict[str, Any]:
    """Riley: GPT-4o + Deepgram nova-3 (multilingual) + ElevenLabs Flash v2.5 (speaks Spanish)."""
    return {
        "name": ASSISTANT_NAME,
        "firstMessage": FIRST_MESSAGE,
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": "gpt-4o",
            "temperature": 0.3,
            "messages": [{"role": "system", "content": load_system_prompt()}],
            "toolIds": tool_ids,
            "tools": [{"type": "endCall"}],
        },
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "multi"},
        "voice": {"provider": "11labs", "model": "eleven_flash_v2_5", "voiceId": "sarah"},
        "server": {
            "url": f"{settings.public_base_url.rstrip('/')}/vapi/events",
            "headers": {"X-Vapi-Secret": settings.vapi_webhook_secret},
            "timeoutSeconds": 20,
            # The end-of-call report is an idempotent upsert on our side, so retries are safe.
            "backoffPlan": {"type": "fixed", "maxRetries": 2, "baseDelaySeconds": 1},
        },
        "serverMessages": ["end-of-call-report", "tool-calls"],
        "maxDurationSeconds": 600,
        # Summaries are no longer generated unless requested (verified on a live call, 2026-09-23).
        "analysisPlan": {"summaryPlan": {"enabled": True, "timeoutSeconds": 10}},
        "backgroundSound": "off",
        "backgroundSpeechDenoisingPlan": {"smartDenoisingPlan": {"enabled": True}},
        # Callers pause while spelling names and reading digits; wait longer before replying.
        "startSpeakingPlan": {
            "waitSeconds": 0.6,
            "transcriptionEndpointingPlan": {
                "onPunctuationSeconds": 0.5,
                "onNoPunctuationSeconds": 1.8,
                "onNumberSeconds": 1.5,
            },
        },
        # Silence handling (replaces the removed silenceTimeoutSeconds): check in twice, then hang up.
        "hooks": [
            {
                "on": "customer.speech.timeout",
                "options": {"timeoutSeconds": 10, "triggerMaxCount": 2, "triggerResetMode": "onUserSpeech"},
                "do": [{
                    "type": "say",
                    "prompt": "The caller has gone quiet. In the language of the conversation, briefly and "
                    "warmly check whether they are still there.",
                }],
            },
            {
                "on": "customer.speech.timeout",
                "options": {"timeoutSeconds": 30, "triggerMaxCount": 1},
                "do": [
                    {
                        "type": "say",
                        "prompt": "The caller has been silent for a long time. In the language of the "
                        "conversation, say you will end the call now and they can call back anytime.",
                    },
                    {"type": "tool", "tool": {"type": "endCall"}},
                ],
            },
        ],
    }


# ---- Vapi API --------------------------------------------------------------------------------

class VapiClient:
    def __init__(self, api_key: str) -> None:
        self._http = httpx.Client(
            base_url=VAPI_API, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = self._http.request(method, path, json=payload)
        if response.is_error:
            raise SetupError(f"{method} {path} failed ({response.status_code}): {response.text}")
        return response.json()


def upsert_tools(client: VapiClient, payloads: list[dict[str, Any]]) -> list[str]:
    """Create or update each tool (matched by function name). Returns tool IDs in order."""
    existing = {
        tool.get("function", {}).get("name"): tool["id"]
        for tool in client.request("GET", "/tool", None)
        if tool.get("type") == "function"
    }
    ids = []
    for payload in payloads:
        name = payload["function"]["name"]
        if name in existing:
            tool = client.request("PATCH", f"/tool/{existing[name]}", payload)
            print(f"  updated tool {name:<24} {tool['id']}")
        else:
            tool = client.request("POST", "/tool", payload)
            print(f"  created tool {name:<24} {tool['id']}")
        ids.append(tool["id"])
    return ids


def upsert_assistant(client: VapiClient, payload: dict[str, Any]) -> str:
    """Create or update the assistant (matched by name). Returns its ID."""
    matches = [a for a in client.request("GET", "/assistant", None) if a.get("name") == ASSISTANT_NAME]
    if matches:
        assistant = client.request("PATCH", f"/assistant/{matches[0]['id']}", payload)
        print(f"  updated assistant {ASSISTANT_NAME!r} {assistant['id']}")
    else:
        assistant = client.request("POST", "/assistant", payload)
        print(f"  created assistant {ASSISTANT_NAME!r} {assistant['id']}")
    return str(assistant["id"])


def bind_phone_number(client: VapiClient, phone_number_id: str, assistant_id: str) -> str | None:
    """Route inbound calls on the number to the assistant. Prints manual steps if the API refuses."""
    try:
        phone = client.request("PATCH", f"/phone-number/{phone_number_id}", {"assistantId": assistant_id})
    except SetupError as exc:
        print(f"  could not bind the phone number via the API: {exc}")
        print("  Do it manually: dashboard.vapi.ai -> Phone Numbers -> select the number -> Inbound Settings ->")
        print(f"  Assistant: {ASSISTANT_NAME} -> Save.")
        return None
    number = phone.get("number") or phone.get("sipUri")
    print(f"  bound phone number {number} -> assistant {assistant_id}")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the payloads without calling Vapi")
    args = parser.parse_args()

    settings = SetupSettings()  # type: ignore[call-arg]  # values come from env / .env
    tools = tool_payloads(settings)

    if args.dry_run:
        redacted = json.dumps(
            {"tools": tools, "assistant": assistant_payload(settings, ["<tool ids>"])}, indent=2, ensure_ascii=False
        ).replace(settings.vapi_webhook_secret, "<VAPI_WEBHOOK_SECRET>")
        print(redacted)
        return 0

    client = VapiClient(settings.vapi_api_key)
    try:
        print("Tools:")
        tool_ids = upsert_tools(client, tools)
        print("Assistant:")
        assistant_id = upsert_assistant(client, assistant_payload(settings, tool_ids))
        print("Phone number:")
        number = bind_phone_number(client, settings.vapi_phone_number_id, assistant_id)
    except SetupError as exc:
        print(f"\nSetup failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nDone. Assistant ID: {assistant_id}")
    if number:
        print(f"Call {number} to talk to Riley.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
