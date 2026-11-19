"""How each kind of OpenAI-compatible server wants its requests. All of them speak /v1/chat/completions, but they
differ in the details: how thinking is switched off, which fields they refuse. Every request to a model goes
through prepare(), so engine differences live here and nowhere else.

Callers ask for no thinking the llama-server way (llm.NO_THINK: chat_template_kwargs.enable_thinking = false);
prepare() rewrites that into the form the server understands."""
from dataclasses import dataclass

# Ways to switch thinking off, as the server expects it:
#   chat_template_kwargs  llama-server, Lucebox, vLLM: passed to the model's chat template
#   reasoning_effort      LocalAI and others that ignore the template kwargs: reasoning_effort "none" as well
#   enable_thinking       a top-level enable_thinking: false instead of the template kwargs
THINK_OFF = ("chat_template_kwargs", "reasoning_effort", "enable_thinking")


@dataclass(frozen=True)
class Engine:
    id: str
    name: str
    think_off: tuple[str, ...]  # switches to try when the server is added, best first; the first that works is kept
    drop: tuple[str, ...] = ()  # request fields the server refuses


ENGINES = {e.id: e for e in [
    Engine("llama", "llama-server (bundled)", ("chat_template_kwargs",)),
    # Any other OpenAI-compatible server. reasoning_effort "none" is tried first: servers that ignore the template
    # kwargs (LocalAI) think anyway without it; servers that reject the value fall back to the template kwargs alone.
    Engine("generic", "OpenAI-compatible server", ("reasoning_effort", "chat_template_kwargs")),
]}


def engine(spec) -> Engine:
    return ENGINES[spec.engine or "generic"] if spec.provider == "server" else ENGINES["llama"]


def prepare(spec, body: dict) -> tuple[dict, dict]:
    """The JSON body and headers for a request to spec's server."""
    if spec.provider != "server":
        return body, {}
    eng = engine(spec)
    body = {k: v for k, v in body.items() if k not in eng.drop}
    if (body.get("chat_template_kwargs") or {}).get("enable_thinking") is False:
        mode = spec.think_off or eng.think_off[0]
        if mode == "reasoning_effort":
            body["reasoning_effort"] = "none"
        elif mode == "enable_thinking":
            kwargs = {k: v for k, v in body.pop("chat_template_kwargs").items() if k != "enable_thinking"}
            body.update({"enable_thinking": False, **({"chat_template_kwargs": kwargs} if kwargs else {})})
    if spec.model:
        body["model"] = spec.model
    return body, headers(spec)


def headers(spec) -> dict:
    return {"Authorization": f"Bearer {spec.key}"} if spec.provider == "server" and spec.key else {}
