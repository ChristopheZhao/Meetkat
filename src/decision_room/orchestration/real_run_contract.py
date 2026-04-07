from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class AgendaFocusPoint:
    title: str
    reason: str
    constraint_ids: list[str]


@dataclass(frozen=True)
class AgendaTurn:
    role: str
    task: str


@dataclass(frozen=True)
class HostAgenda:
    focus_points: list[AgendaFocusPoint]
    turns: list[AgendaTurn]
    open_questions: list[str]
    no_new_constraints: bool


def build_meeting_brief(topic: str, next_focus: str) -> dict:
    return {
        "topic": topic,
        "next_focus": next_focus,
        "objective": "align on an MVP direction for a multi-agent meeting room",
        "constraints": [
            {
                "id": "C1",
                "text": "The product is a multi-agent online meeting room for product and technical discussion.",
            },
            {
                "id": "C2",
                "text": "Human participants can join, but the early phase is agent-led automation first.",
            },
            {
                "id": "C3",
                "text": "The meeting room must expose a visible live discussion stream.",
            },
            {
                "id": "C4",
                "text": "Hybrid MAS is the primary implementation approach.",
            },
            {
                "id": "C5",
                "text": "FSM is guardrail-only and not the intelligent core.",
            },
            {
                "id": "C6",
                "text": "Backend/runtime must stay decoupled from MAS algorithms.",
            },
        ],
    }


def build_host_prompts(brief: dict) -> tuple[str, str]:
    schema = {
        "focus_points": [
            {
                "title": "short title",
                "reason": "one or two sentences grounded in the brief",
                "constraint_ids": ["C1", "C2"],
            }
        ],
        "turns": [
            {
                "role": "implementation_specialist",
                "task": "propose the minimum runtime shape that preserves replay and override visibility",
            }
        ],
        "open_questions": ["question if information is missing"],
        "no_new_constraints": True,
    }
    system_prompt = (
        "You are the host agent in a multi-agent product decision room. "
        "You must only use information present in the meeting brief. "
        "Treat meeting_brief.validated_context as authoritative pre-room context. "
        "Do not restate validated_context facts as open_questions, blockers, or missing inputs. "
        "Do not invent deployment constraints, security constraints, infra constraints, "
        "compliance constraints, or business constraints that are not explicitly provided. "
        "If information is missing, put it into open_questions instead of making it up. "
        "Return exactly one JSON object and nothing else."
    )
    user_prompt = (
        "Meeting brief:\n"
        f"{json.dumps(brief, ensure_ascii=False, indent=2)}\n\n"
        "Task:\n"
        "- Produce exactly 3 focus_points for the next round.\n"
        "- Each focus point must reference only constraint_ids from the brief.\n"
        "- Produce 1-3 turns that decide which candidate specialists should speak this round.\n"
        "- Each turns.role must be chosen from brief.candidate_specialists.role.\n"
        "- Each turns.task must be concrete, brief-grounded, and tell that specialist what to answer in this round.\n"
        "- Order turns to reflect the host-led speaking sequence for this round.\n"
        "- Keep each title concise.\n"
        "- Keep each reason factual and brief-grounded.\n"
        "- Do not emit open_questions for facts already covered by brief.validated_context.\n"
        "- If information is missing, record up to 3 open_questions.\n"
        "- Set no_new_constraints=true only if you did not add any hard constraint.\n\n"
        "Output schema example:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
    return system_prompt, user_prompt


def extract_json_object(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start >= end:
        raise ValueError("response does not contain a JSON object")
    return text[start : end + 1]


def parse_host_agenda(
    raw: str,
    allowed_constraint_ids: set[str],
    allowed_specialist_roles: set[str],
) -> HostAgenda:
    payload = json.loads(extract_json_object(raw))
    focus_points_raw = payload.get("focus_points")
    turns_raw = payload.get("turns")
    open_questions_raw = payload.get("open_questions", [])
    no_new_constraints = payload.get("no_new_constraints")

    if not isinstance(focus_points_raw, list) or len(focus_points_raw) != 3:
        raise ValueError("focus_points must be a list with exactly 3 items")
    if not isinstance(turns_raw, list) or not turns_raw:
        raise ValueError("turns must be a non-empty list")
    if len(turns_raw) > 3:
        raise ValueError("turns must contain at most 3 items")
    if not isinstance(open_questions_raw, list):
        raise ValueError("open_questions must be a list")
    if len(open_questions_raw) > 3:
        raise ValueError("open_questions must contain at most 3 items")
    if no_new_constraints is not True:
        raise ValueError("no_new_constraints must be true")

    focus_points: list[AgendaFocusPoint] = []
    for idx, item in enumerate(focus_points_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"focus_points[{idx}] must be an object")

        title = item.get("title")
        reason = item.get("reason")
        constraint_ids = item.get("constraint_ids")

        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"focus_points[{idx}].title must be non-empty")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"focus_points[{idx}].reason must be non-empty")
        if not isinstance(constraint_ids, list) or not constraint_ids:
            raise ValueError(f"focus_points[{idx}].constraint_ids must be a non-empty list")

        normalized_ids: list[str] = []
        for constraint_id in constraint_ids:
            if not isinstance(constraint_id, str) or not constraint_id.strip():
                raise ValueError(
                    f"focus_points[{idx}].constraint_ids contains invalid id"
                )
            if constraint_id not in allowed_constraint_ids:
                raise ValueError(
                    f"focus_points[{idx}] references unknown constraint id: {constraint_id}"
                )
            normalized_ids.append(constraint_id)

        focus_points.append(
            AgendaFocusPoint(
                title=title.strip(),
                reason=reason.strip(),
                constraint_ids=normalized_ids,
            )
        )

    turns: list[AgendaTurn] = []
    seen_roles: set[str] = set()
    for idx, item in enumerate(turns_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"turns[{idx}] must be an object")

        role = item.get("role")
        task = item.get("task")
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"turns[{idx}].role must be non-empty")
        normalized_role = role.strip().lower()
        if normalized_role not in allowed_specialist_roles:
            raise ValueError(f"turns[{idx}] references unknown specialist role: {role}")
        if normalized_role in seen_roles:
            raise ValueError(f"turns[{idx}].role duplicates an earlier specialist role")
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"turns[{idx}].task must be non-empty")
        turns.append(AgendaTurn(role=normalized_role, task=task.strip()))
        seen_roles.add(normalized_role)

    open_questions: list[str] = []
    for idx, question in enumerate(open_questions_raw, start=1):
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"open_questions[{idx}] must be non-empty")
        open_questions.append(question.strip())

    return HostAgenda(
        focus_points=focus_points,
        turns=turns,
        open_questions=open_questions,
        no_new_constraints=True,
    )
