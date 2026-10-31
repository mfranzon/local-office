"""Runs skills. The assistant model first routes a message to a skill (seeing only names and descriptions), then
follows that skill's instructions, calling tools until it can answer. Routing with the assistant measured 18/18 on
English and Italian requests (0.5-0.8 s), against 13/18 for a 1.2B model."""
import json
import logging
from collections.abc import AsyncIterator
from datetime import date

from . import llm, tools
from .skills import Skill

log = logging.getLogger(__name__)
MAX_STEPS = 10

ROUTER = """You decide whether an office request needs a skill. Skills:
{skills}

Pick a skill only when the user asks for the task the skill performs (make a report, a table or a file, list
deadlines). A question that asks for a fact is "none", even when it mentions invoices or deadlines: the assistant
answers it directly from the documents.
Examples:
- "When is invoice 118 due?" -> none
- "What did Anna ask in her letter?" -> none
- "Who is the project lead?" -> none
- "What is due this month?" -> deadlines-report
- "List all invoices in Excel" -> invoice-table"""

SYSTEM = """You are an office assistant running the skill "{name}". Today is {today}.
Follow the skill's instructions, using the tools to read the office's documents and wiki.
You can only read; the one thing you can create is a file for the user to download (create_file). Files get a
download button automatically, so never write links to them.
Cite facts with the source numbers that tool results give, e.g. [2]. Never invent facts, ids or numbers.{references}

## Skill instructions
{instructions}

## Before you answer
- Do every step of the skill instructions that applies, including create_file when a step calls for it.
- Write in the language of the user's request, not of the documents, unless the skill says otherwise.
  The request: "{request}"
- Answer concisely, in Markdown."""


async def route(message: str, skills: list[Skill]) -> Skill | None:
    if not skills:
        return None
    listing = "\n".join(f"- {s.name}: {s.description}" for s in skills)
    schema = {"type": "object", "required": ["skill"],
              "properties": {"skill": {"enum": [s.name for s in skills] + ["none"]}}}
    try:
        out = await llm.chat("assistant", [{"role": "system", "content": ROUTER.format(skills=listing)},
                                       {"role": "user", "content": message}],
                             max_tokens=40, response_format={"type": "json_schema", "json_schema": {"name": "route", "schema": schema}},
                             **llm.NO_THINK)
        name = json.loads(out)["skill"]
    except Exception:
        log.exception("routing failed")
        return None
    return next((s for s in skills if s.name == name), None)


def _describe_call(args: dict) -> str:
    """A short, human-readable label of the step, for the UI."""
    return next((str(args[k])[:80] for k in ("query", "text", "name", "filename", "doc_id") if args.get(k)), "")


async def run(skill: Skill, history: list[dict]) -> AsyncIterator[dict]:
    """Streams events: skill, step (one per tool call), sources, token (the answer), file (created files)."""
    run_state = tools.Run()
    refs = f"\nThe skill has reference files you can read with read_skill_file: {', '.join(skill.references)}." if skill.references else ""
    request = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")[:300]
    messages = [{"role": "system", "content": SYSTEM.format(name=skill.name, today=date.today().isoformat(),
                                                            instructions=skill.instructions, references=refs, request=request)},
                *history[-8:]]
    specs = tools.schemas()
    if skill.references:
        specs.append({"type": "function", "function": {
            "name": "read_skill_file", "description": "Read one of the skill's reference files.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}})
    yield {"type": "skill", "name": skill.name}

    answer = ""
    for step in range(MAX_STEPS + 1):
        last = step == MAX_STEPS
        msg = await llm.complete("assistant", messages, max_tokens=2500, **llm.NO_THINK,
                                 **({} if last else {"tools": specs}))
        calls = msg.get("tool_calls") or []
        if not calls or last:
            answer = msg.get("content") or ""
            break
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            yield {"type": "step", "tool": name, "detail": _describe_call(args)}
            if name == "read_skill_file":
                result = skill.read_reference(str(args.get("path", "")))
            else:
                result = await tools.call(run_state, name, args)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})

    yield {"type": "sources", "sources": run_state.sources}
    yield {"type": "token", "text": answer.strip() or "(no answer)"}
    for f in run_state.files:
        yield {"type": "file", **f}
