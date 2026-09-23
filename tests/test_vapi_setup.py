"""Keep the Vapi config (scripts/vapi_setup.py), the prompt, and the server in sync."""

import pytest

from app.schemas import REQUIRED_FIELDS, PatientCreate
from app.services.voice_tools import TOOLS
from scripts import vapi_setup


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> vapi_setup.SetupSettings:
    monkeypatch.setenv("VAPI_API_KEY", "key")
    monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "phone-id")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.up.railway.app/")
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "a-long-test-secret")
    return vapi_setup.SetupSettings()


def test_every_configured_tool_exists_on_the_server(settings: vapi_setup.SetupSettings) -> None:
    names = {tool["function"]["name"] for tool in vapi_setup.tool_payloads(settings)}
    assert names == set(TOOLS)


def test_tool_schema_matches_patient_schema() -> None:
    assert set(vapi_setup.PATIENT_PROPERTIES) == set(PatientCreate.model_fields)
    assert vapi_setup.REQUIRED_PATIENT_FIELDS == list(REQUIRED_FIELDS)


def test_tools_post_to_the_webhook_with_the_secret(settings: vapi_setup.SetupSettings) -> None:
    for tool in vapi_setup.tool_payloads(settings):
        assert tool["server"]["url"] == "https://example.up.railway.app/vapi/tools"
        assert tool["server"]["headers"] == {"X-Vapi-Secret": "a-long-test-secret"}


def test_system_prompt_is_stripped_of_design_comments_and_names_every_tool() -> None:
    prompt = vapi_setup.load_system_prompt()
    assert "<!--" not in prompt
    assert '{{"now" | date: "%B %d, %Y", "America/New_York"}}' in prompt
    assert "{{customer.number}}" in prompt
    for name in TOOLS:
        assert name in prompt
    assert "endCall" in prompt


def test_assistant_uses_end_call_and_events_webhook(settings: vapi_setup.SetupSettings) -> None:
    payload = vapi_setup.assistant_payload(settings, ["t1"])
    assert payload["model"]["toolIds"] == ["t1"]
    assert {"type": "endCall"} in payload["model"]["tools"]
    assert payload["server"]["url"] == "https://example.up.railway.app/vapi/events"
