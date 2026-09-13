"""Create (or update) the Vapi assistant from this repository.

Keeping the assistant definition in code rather than clicking through the
dashboard means the prompt, the tool schemas and the server URL are all version
controlled and reproducible.

Usage:
    python scripts/create_assistant.py --dry-run          # print the payload
    python scripts/create_assistant.py                    # create a new one
    python scripts/create_assistant.py --update <id>      # update in place

Required environment variables:
    VAPI_PRIVATE_KEY   Vapi dashboard -> Settings -> API Keys -> Private Key
    PUBLIC_BASE_URL    Public HTTPS base of this API, e.g. https://x.onrender.com
    VAPI_SERVER_SECRET Shared secret, must match the API's own env var
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

VAPI_API = "https://api.vapi.ai"
PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "assistant_prompt.md"

# Field descriptions are written for the LLM, telling it the format to emit.
# The API normalises spoken forms anyway, but steering the model up front means
# fewer validation round-trips mid-call.
_PATIENT_FIELDS = {
    "first_name": {"type": "string", "description": "Patient's first name."},
    "last_name": {"type": "string", "description": "Patient's last name."},
    "date_of_birth": {
        "type": "string",
        "description": "Date of birth as MM/DD/YYYY. Must not be in the future.",
    },
    "sex": {
        "type": "string",
        "enum": ["Male", "Female", "Other", "Decline to Answer"],
        "description": "One of the four accepted values.",
    },
    "phone_number": {
        "type": "string",
        "description": "10-digit US phone number, digits only, e.g. 7132101234.",
    },
    "email": {"type": "string", "description": "Email address. Optional."},
    "address_line_1": {"type": "string", "description": "Street address."},
    "address_line_2": {
        "type": "string",
        "description": "Apartment, suite or unit. Optional.",
    },
    "city": {"type": "string", "description": "City name."},
    "state": {
        "type": "string",
        "description": "Two-letter US state code, e.g. TX. Full names accepted.",
    },
    "zip_code": {"type": "string", "description": "5-digit ZIP, or ZIP+4."},
    "insurance_provider": {
        "type": "string", "description": "Insurance company name. Optional.",
    },
    "insurance_member_id": {
        "type": "string", "description": "Member or subscriber ID. Optional.",
    },
    "preferred_language": {
        "type": "string", "description": "Preferred language. Defaults to English.",
    },
    "emergency_contact_name": {
        "type": "string", "description": "Emergency contact full name. Optional.",
    },
    "emergency_contact_phone": {
        "type": "string",
        "description": "Emergency contact 10-digit US phone. Optional.",
    },
}

_REQUIRED = [
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
]


def load_system_prompt() -> str:
    """Pull the system message out of the fenced block in the prompt doc."""
    text = PROMPT_FILE.read_text(encoding="utf-8")
    match = re.search(r"## System message\s*\n+```\n(.*?)\n```", text, re.DOTALL)
    if not match:
        sys.exit(f"Could not find the fenced system message in {PROMPT_FILE}")
    return match.group(1).strip()


def build_tools(tools_url: str, secret: str) -> list[dict]:
    server = {"url": tools_url}
    if secret:
        server["secret"] = secret

    update_props = dict(_PATIENT_FIELDS)
    update_props["phone_number_lookup"] = {
        "type": "string",
        "description": (
            "The existing patient's phone number, used to find their record. "
            "Omit to use the number the caller is calling from."
        ),
    }

    return [
        {
            "type": "function",
            "server": server,
            "function": {
                "name": "lookup_patient",
                "description": (
                    "Check whether a patient record already exists for this "
                    "caller. Call this immediately after greeting them, before "
                    "asking any questions. The caller's own number is supplied "
                    "automatically, so phone_number can be omitted."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "phone_number": {
                            "type": "string",
                            "description": (
                                "Only pass this if looking up a number other "
                                "than the one the caller is calling from."
                            ),
                        }
                    },
                    "required": [],
                },
            },
            "messages": [
                {
                    "type": "request-start",
                    "content": "Let me pull that up.",
                    "blocking": False,
                }
            ],
        },
        {
            "type": "function",
            "server": server,
            "function": {
                "name": "register_patient",
                "description": (
                    "Save a new patient record. Call this ONLY after reading "
                    "the information back and getting the caller's confirmation. "
                    "If it returns validation_errors, re-ask only for those "
                    "fields and call it again."
                ),
                "parameters": {
                    "type": "object",
                    "properties": _PATIENT_FIELDS,
                    "required": _REQUIRED,
                },
            },
            "messages": [
                {
                    "type": "request-start",
                    "content": "Great, saving that now.",
                    "blocking": False,
                },
                {
                    "type": "request-failed",
                    "content": (
                        "I'm sorry, I couldn't save that just now. Could you "
                        "give us a call back in a few minutes?"
                    ),
                },
            ],
        },
        {
            "type": "function",
            "server": server,
            "function": {
                "name": "update_patient",
                "description": (
                    "Update an existing patient record. Use when lookup_patient "
                    "found a record and the caller wants to change details. "
                    "Pass only the fields being changed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": update_props,
                    "required": [],
                },
            },
            "messages": [
                {
                    "type": "request-start",
                    "content": "Updating that for you.",
                    "blocking": False,
                }
            ],
        },
    ]


def build_assistant(base_url: str, secret: str) -> dict:
    tools_url = f"{base_url.rstrip('/')}/vapi/tools"
    return {
        "name": "Meridian Patient Intake",
        "firstMessage": (
            "Thanks for calling Meridian Family Health, this is Riley. "
            "I can get you registered as a new patient - is that what you're "
            "calling about today?"
        ),
        "model": {
            "provider": "openai",
            "model": "gpt-4o",
            # Low but not zero: enough variation to sound human, tight enough
            # that it does not improvise around the required fields.
            "temperature": 0.4,
            "messages": [{"role": "system", "content": load_system_prompt()}],
            "tools": build_tools(tools_url, secret),
        },
        "voice": {
            "provider": "11labs",
            "voiceId": "burt",
            # Trades a little latency for noticeably smoother prosody on the
            # long read-back, which is the most scrutinised part of the call.
            "model": "eleven_turbo_v2_5",
        },
        "transcriber": {
            "provider": "deepgram",
            "model": "nova-2",
            "language": "en-US",
            # Names, street names and insurance brands are the words Deepgram
            # is most likely to mangle, so bias it toward them.
            "keywords": ["Meridian:2", "Riley:1"],
        },
        # Server-level events (end-of-call transcript logging) go to the same
        # endpoint; it ignores anything that is not a tool call.
        "server": (
            {"url": tools_url, "secret": secret} if secret else {"url": tools_url}
        ),
        "serverMessages": ["tool-calls", "end-of-call-report"],
        "endCallFunctionEnabled": True,
        "endCallPhrases": ["goodbye", "bye now", "take care"],
        "silenceTimeoutSeconds": 20,
        "maxDurationSeconds": 600,
        "backgroundSound": "office",
        # Let the caller cut in; interruption handling is an explicit criterion.
        "startSpeakingPlan": {"waitSeconds": 0.4},
        "stopSpeakingPlan": {"numWords": 2},
    }


def call_vapi(path: str, payload: dict, api_key: str, method: str = "POST") -> dict:
    req = urllib.request.Request(
        f"{VAPI_API}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Vapi's API sits behind Cloudflare, which blocks the default
            # urllib User-Agent outright (Cloudflare error 1010) before the
            # request ever reaches Vapi. A normal browser-shaped UA passes.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"Vapi returned {exc.code}: {exc.read().decode()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the payload without calling Vapi.")
    parser.add_argument("--update", metavar="ASSISTANT_ID",
                        help="Update an existing assistant instead of creating.")
    args = parser.parse_args()

    base_url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    secret = os.environ.get("VAPI_SERVER_SECRET", "").strip()
    if not base_url:
        sys.exit("PUBLIC_BASE_URL is not set (e.g. https://your-app.onrender.com)")

    payload = build_assistant(base_url, secret)

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return

    api_key = os.environ.get("VAPI_PRIVATE_KEY", "").strip()
    if not api_key:
        sys.exit("VAPI_PRIVATE_KEY is not set.")

    if args.update:
        result = call_vapi(f"/assistant/{args.update}", payload, api_key, "PATCH")
        print(f"Updated assistant {result['id']}")
    else:
        result = call_vapi("/assistant", payload, api_key)
        print(f"Created assistant {result['id']}")
        print("\nNext: attach it to your phone number, either in the dashboard "
              "or with\n")
        print(f"  curl -X PATCH {VAPI_API}/phone-number/<PHONE_NUMBER_ID> \\")
        print("    -H \"Authorization: Bearer $VAPI_PRIVATE_KEY\" \\")
        print("    -H 'Content-Type: application/json' \\")
        print(f"    -d '{{\"assistantId\": \"{result['id']}\"}}'")


if __name__ == "__main__":
    main()
