"""Vapi tool-call webhook.

Vapi's assistant calls these tools mid-conversation. Two payload shapes exist in
the wild, so both are accepted:

  current : {"message": {"type": "tool-calls",
                         "toolCallList"|"toolCalls": [
                             {"id": ..., "function": {"name", "arguments"}}]}}
  legacy  : {"message": {"type": "function-call",
                         "functionCall": {"name", "parameters"}}}

Results are returned as {"results": [{"toolCallId": ..., "result": "..."}]}.
The `result` string is read by the LLM, not the caller, so it is written as a
plain instruction the model can act on - including on failure, where it names
the offending field so the agent re-prompts for that field specifically.
"""
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.schemas import PatientCreate, PatientRead, PatientUpdate
from app.validators import ValidationProblem, normalise_phone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vapi", tags=["vapi"])


def verify_secret(x_vapi_secret: Optional[str] = Header(None)) -> None:
    """Reject tool calls that do not carry the shared secret.

    Left permissive by default so the webhook can be wired up before the secret
    is configured; set ALLOW_UNAUTHENTICATED_TOOLS=false once it is.
    """
    expected = settings.vapi_server_secret
    if not expected or settings.allow_unauthenticated_tools:
        if expected and x_vapi_secret != expected:
            logger.warning("Tool call with bad secret allowed (permissive mode)")
        return
    if x_vapi_secret != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid server secret.")


def _parse_args(raw: Any) -> dict:
    """Arguments arrive as a dict or a JSON string depending on the provider."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_tool_calls(body: dict) -> list[dict]:
    """Normalise either payload shape into [{id, name, args}]."""
    message = body.get("message") or {}
    raw = message.get("toolCallList") or message.get("toolCalls") or []

    calls = []
    for item in raw:
        fn = item.get("function") or {}
        calls.append({
            "id": item.get("id"),
            "name": fn.get("name"),
            "args": _parse_args(fn.get("arguments", {})),
        })

    if not calls and message.get("functionCall"):  # legacy shape
        fc = message["functionCall"]
        calls.append({
            "id": fc.get("id"),
            "name": fc.get("name"),
            "args": _parse_args(fc.get("parameters", {})),
        })
    return calls


def _caller_number(body: dict) -> Optional[str]:
    """The number the caller dialled in from, if Vapi supplied it."""
    call = (body.get("message") or {}).get("call") or {}
    raw = (call.get("customer") or {}).get("number")
    if not raw:
        return None
    try:
        return normalise_phone(raw)
    except ValidationProblem:
        return None


def _summarise(patient) -> dict:
    """Compact record for the LLM - full detail would bloat the prompt."""
    return {
        "patient_id": patient.patient_id,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "date_of_birth": patient.date_of_birth.strftime("%m/%d/%Y"),
        "phone_number": patient.phone_number,
        "city": patient.city,
        "state": patient.state,
    }


def _readable_errors(exc: Exception) -> dict:
    """Turn a ValidationError into {field: spoken-friendly reason}."""
    if isinstance(exc, ValidationProblem):
        return {"_": str(exc)}

    problems = {}
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "_"
        msg = err.get("msg", "is invalid")
        # Pydantic prefixes messages raised from our validators; strip the noise.
        for prefix in ("Value error, ", "Assertion failed, "):
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
        if err["type"] == "missing":
            msg = "This required field is missing."
        problems[field] = msg
    return problems


# --- Tool implementations ------------------------------------------------

def _tool_lookup_patient(args: dict, db: Session, caller: Optional[str]) -> str:
    phone = args.get("phone_number") or caller
    if not phone:
        return json.dumps({
            "found": False,
            "message": "No phone number available. Ask the caller for theirs.",
        })
    try:
        phone = normalise_phone(phone)
    except ValidationProblem as exc:
        return json.dumps({"found": False, "message": str(exc)})

    existing = crud.find_by_phone(db, phone)
    if existing is None:
        return json.dumps({
            "found": False,
            "message": "No existing record. Proceed with a new registration.",
        })
    return json.dumps({
        "found": True,
        "patient": _summarise(existing),
        "message": (
            "An existing record was found for "
            f"{existing.first_name} {existing.last_name}. Ask whether they "
            "would like to update it instead of creating a new one."
        ),
    })


def _tool_register_patient(args: dict, db: Session, caller: Optional[str]) -> str:
    # Fall back to the caller ID if the agent did not capture a phone number.
    if not args.get("phone_number") and caller:
        args["phone_number"] = caller

    try:
        payload = PatientCreate(**args)
    except (ValidationError, ValidationProblem) as exc:
        problems = _readable_errors(exc)
        logger.info("REGISTRATION_REJECTED problems=%s", problems)
        return json.dumps({
            "success": False,
            "validation_errors": problems,
            "message": (
                "The record was not saved. Re-prompt the caller for only the "
                "fields listed in validation_errors, then call this tool again."
            ),
        })

    try:
        patient = crud.create_patient(db, payload)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database write failed during registration")
        return json.dumps({
            "success": False,
            "message": (
                "The database could not be reached. Apologise, tell the caller "
                "their information was not saved, and ask them to call back."
            ),
        })

    logger.info(
        "REGISTRATION_COMPLETE payload=%s",
        PatientRead.model_validate(patient).model_dump(mode="json"),
    )
    return json.dumps({
        "success": True,
        "patient_id": patient.patient_id,
        "message": (
            f"Registration saved for {patient.first_name} {patient.last_name}. "
            "Confirm to the caller that they are all set."
        ),
    })


def _tool_update_patient(args: dict, db: Session, caller: Optional[str]) -> str:
    args = dict(args)
    patient_id = args.pop("patient_id", None)
    phone = args.pop("phone_number_lookup", None) or caller

    patient = None
    if patient_id:
        patient = crud.get_patient(db, patient_id)
    elif phone:
        try:
            patient = crud.find_by_phone(db, normalise_phone(phone))
        except ValidationProblem:
            patient = None

    if patient is None:
        return json.dumps({
            "success": False,
            "message": "No matching record found. Register them as a new patient.",
        })

    try:
        payload = PatientUpdate(**args)
    except (ValidationError, ValidationProblem) as exc:
        return json.dumps({
            "success": False,
            "validation_errors": _readable_errors(exc),
            "message": "Re-prompt only for the fields listed in validation_errors.",
        })

    try:
        patient = crud.update_patient(db, patient, payload)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database write failed during update")
        return json.dumps({
            "success": False,
            "message": "The update could not be saved. Apologise to the caller.",
        })

    return json.dumps({
        "success": True,
        "patient_id": patient.patient_id,
        "message": f"Record updated for {patient.first_name} {patient.last_name}.",
    })


TOOLS = {
    "lookup_patient": _tool_lookup_patient,
    "register_patient": _tool_register_patient,
    "update_patient": _tool_update_patient,
}


@router.post("/tools", summary="Vapi tool-call webhook")
async def handle_tool_call(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(verify_secret),
):
    body: dict[str, Any] = await request.json()
    message = body.get("message") or {}
    msg_type = message.get("type")

    calls = _extract_tool_calls(body)
    if not calls:
        # Vapi also posts status-update / end-of-call-report events here. Log
        # the transcript for observability and acknowledge without acting.
        if msg_type == "end-of-call-report":
            logger.info(
                "CALL_ENDED reason=%s transcript=%s",
                message.get("endedReason"),
                message.get("transcript"),
            )
        else:
            logger.debug("Non-tool Vapi event ignored: %s", msg_type)
        return {"results": []}

    caller = _caller_number(body)
    results = []
    for call in calls:
        handler = TOOLS.get(call["name"])
        if handler is None:
            logger.warning("Unknown tool requested: %s", call["name"])
            result = json.dumps({
                "success": False,
                "message": "That tool does not exist.",
            })
        else:
            logger.info("TOOL_CALL name=%s args=%s", call["name"], call["args"])
            try:
                result = handler(call["args"], db, caller)
            except Exception:  # never surface a stack trace into a live call
                logger.exception("Tool %s raised", call["name"])
                result = json.dumps({
                    "success": False,
                    "message": (
                        "An internal error occurred. Apologise to the caller and "
                        "ask them to try again shortly."
                    ),
                })
        results.append({"toolCallId": call["id"], "result": result})

    return {"results": results}
