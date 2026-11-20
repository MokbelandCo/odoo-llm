"""OpenAI model capability registry.

``GET /v1/models`` only returns ``id``, ``created`` and ``owned_by``; it does
not say which endpoint, tool or parameter a model accepts. Capabilities
therefore come from this registry, keyed by exact base model id.

Entries were checked against the developers.openai.com model pages and
against the live API (invalid-value probes and minimal text/tool requests)
on 2026-11-19. When OpenAI ships a model that is not listed here, it is
either inferred from an explicitly named family (``source="family"``,
Responses only, no optional parameters) or reported as unknown until an
administrator runs the capability probe on the ``llm.model`` record.
"""

import re
from dataclasses import asdict, dataclass, replace

RESPONSES = "responses"
CHAT_COMPLETIONS = "chat_completions"
ENDPOINTS = (RESPONSES, CHAT_COMPLETIONS)
ENDPOINT_PATHS = {
    RESPONSES: "/v1/responses",
    CHAT_COMPLETIONS: "/v1/chat/completions",
}

KIND_CHAT = "chat"
KIND_EMBEDDING = "embedding"
KIND_TRANSCRIPTION = "transcription"
KIND_SPEECH = "speech"
KIND_IMAGE = "image_generation"
KIND_REALTIME = "realtime"
KIND_AUDIO_CHAT = "audio_chat"
KIND_MODERATION = "moderation"
KIND_SEARCH = "search"
KIND_COMPLETION = "completion"
KIND_UNKNOWN = "unknown"

KIND_LABELS = {
    KIND_CHAT: "chat",
    KIND_EMBEDDING: "embedding",
    KIND_TRANSCRIPTION: "speech-to-text",
    KIND_SPEECH: "text-to-speech",
    KIND_IMAGE: "image generation",
    KIND_REALTIME: "realtime/live voice",
    KIND_AUDIO_CHAT: "audio-only chat (requires audio input or output)",
    KIND_MODERATION: "moderation",
    KIND_SEARCH: "web search (search-specific request options)",
    KIND_COMPLETION: "legacy completions",
    KIND_UNKNOWN: "unknown",
}

#: Chat Completions function-tool / sampling policies.
ALWAYS = "always"
EFFORT_NONE = "effort_none"
NEVER = "never"

SOURCE_REGISTRY = "registry"
SOURCE_FAMILY = "family"
SOURCE_PROBE = "probe"
SOURCE_UNKNOWN = "unknown"

SAMPLING_PARAMS = ("temperature", "top_p", "logprobs", "top_logprobs")
VERBOSITY_VALUES = ("low", "medium", "high")


@dataclass(frozen=True)
class OpenAIModelCapabilities:
    """What one OpenAI model accepts, per endpoint."""

    model_id: str
    base_id: str
    kind: str
    source: str
    endpoints: tuple = ()
    vision: bool = False
    #: Function tools on the Responses API.
    function_tools: bool = False
    #: Function tools on Chat Completions: ``always``, ``effort_none`` or ``never``.
    cc_function_tools: str = NEVER
    streaming: bool = True
    reasoning: bool = False
    #: Accepted ``reasoning.effort`` values on Responses. Empty = not validated.
    efforts: tuple = ()
    #: Accepted ``reasoning_effort`` values on Chat Completions.
    cc_efforts: tuple = ()
    default_effort: str = None
    cc_default_effort: str = None
    #: temperature/top_p/logprobs: ``always``, ``effort_none`` or ``never``.
    sampling: str = NEVER
    verbosity: bool = False
    note: str = ""

    @property
    def chat_capable(self):
        # Audio-only chat models (gpt-4o-audio-preview) speak Chat Completions
        # with input_audio; they are chat-capable but not Responses-capable.
        return self.kind in (KIND_CHAT, KIND_AUDIO_CHAT) and bool(self.endpoints)

    @property
    def direct_audio_chat(self):
        return self.kind == KIND_AUDIO_CHAT and self.supports_endpoint(CHAT_COMPLETIONS)

    @property
    def verified(self):
        return self.source in (SOURCE_REGISTRY, SOURCE_PROBE)

    def supports_endpoint(self, endpoint):
        return endpoint in self.endpoints

    def efforts_for(self, endpoint):
        return self.cc_efforts if endpoint == CHAT_COMPLETIONS else self.efforts

    def default_effort_for(self, endpoint):
        if endpoint == CHAT_COMPLETIONS and self.cc_default_effort:
            return self.cc_default_effort
        return self.default_effort

    def effective_effort(self, endpoint, effort=None):
        return effort or self.default_effort_for(endpoint)

    def tools_supported(self, endpoint, effort=None):
        """Can function tools be sent on ``endpoint`` with this effort?"""
        if endpoint not in self.endpoints:
            return False
        if endpoint == RESPONSES:
            return self.function_tools
        if self.cc_function_tools == ALWAYS:
            return True
        if self.cc_function_tools == EFFORT_NONE:
            return self.effective_effort(endpoint, effort) == "none"
        return False

    def sampling_allowed(self, endpoint, effort=None):
        if self.sampling == ALWAYS:
            return True
        if self.sampling == EFFORT_NONE:
            return self.effective_effort(endpoint, effort) == "none"
        return False

    def to_dict(self):
        data = asdict(self)
        data["chat_capable"] = self.chat_capable
        data["verified"] = self.verified
        return data


def _chat(**values):
    base = {
        "kind": KIND_CHAT,
        "endpoints": (RESPONSES, CHAT_COMPLETIONS),
        "vision": True,
        "function_tools": True,
        "streaming": True,
    }
    base.update(values)
    return base


def _gpt4(vision=True):
    return _chat(vision=vision, cc_function_tools=ALWAYS, sampling=ALWAYS)


def _responses_only(**values):
    values.setdefault("endpoints", (RESPONSES,))
    values.setdefault("reasoning", True)
    return _chat(**values)


_GPT5_EFFORTS = ("minimal", "low", "medium", "high")
_GPT51_EFFORTS = ("none", "low", "medium", "high")
_NONE_TO_XHIGH = ("none", "low", "medium", "high", "xhigh")
_NONE_TO_MAX = ("none", "low", "medium", "high", "xhigh", "max")
_LOW_TO_MAX = ("low", "medium", "high", "xhigh", "max")
_LOW_TO_XHIGH = ("low", "medium", "high", "xhigh")
_PRO_EFFORTS = ("medium", "high", "xhigh")


def _gpt5():
    return _chat(
        reasoning=True,
        efforts=_GPT5_EFFORTS,
        cc_efforts=_GPT5_EFFORTS,
        default_effort="medium",
        cc_function_tools=ALWAYS,
        verbosity=True,
    )


def _effort_none_default(efforts, cc_efforts, cc_tools):
    return _chat(
        reasoning=True,
        efforts=efforts,
        cc_efforts=cc_efforts,
        default_effort="none",
        cc_function_tools=cc_tools,
        sampling=EFFORT_NONE,
        verbosity=True,
    )


def _gpt56_or_gpt6_none():
    """Models that accept effort ``none`` but default to ``medium``."""
    return _chat(
        reasoning=True,
        efforts=_NONE_TO_MAX,
        cc_efforts=_NONE_TO_XHIGH,
        default_effort="medium",
        cc_function_tools=EFFORT_NONE,
        sampling=EFFORT_NONE,
        verbosity=True,
    )


def _gpt6_reasoning_only():
    """GPT-6 Astra / 6.1 Sol / 6.2: no effort ``none``, tools need Responses."""
    return _chat(
        reasoning=True,
        efforts=_LOW_TO_MAX,
        cc_efforts=_LOW_TO_XHIGH,
        default_effort="medium",
        cc_function_tools=NEVER,
        verbosity=True,
    )


def _pro(efforts=_PRO_EFFORTS, streaming=True, default="medium", verbosity=True):
    return _responses_only(
        efforts=efforts,
        default_effort=default,
        streaming=streaming,
        verbosity=verbosity,
        note="Responses only; long-running requests can take minutes.",
    )


CHAT_MODELS = {
    # GPT-4 class: both endpoints, sampling parameters always accepted.
    "gpt-4o": _gpt4(),
    "gpt-4o-mini": _gpt4(),
    "gpt-4.1": _gpt4(),
    "gpt-4.1-mini": _gpt4(),
    "gpt-4.1-nano": _gpt4(),
    "gpt-4-turbo": _gpt4(),
    "gpt-4": _gpt4(vision=False),
    "gpt-3.5-turbo": _gpt4(vision=False),
    "chat-latest": _chat(
        reasoning=True,
        efforts=("medium",),
        cc_efforts=("medium",),
        default_effort="medium",
        cc_function_tools=ALWAYS,
    ),
    # o-series
    "o3": _chat(
        reasoning=True,
        efforts=("low", "medium", "high"),
        cc_efforts=_LOW_TO_XHIGH,
        default_effort="medium",
        cc_function_tools=ALWAYS,
    ),
    "o3-pro": _pro(
        efforts=("low", "medium", "high"), streaming=False, verbosity=False
    ),
    # GPT-5
    "gpt-5": _gpt5(),
    "gpt-5-mini": _gpt5(),
    "gpt-5-nano": _gpt5(),
    "gpt-5-pro": _pro(efforts=("high",), default="high"),
    "gpt-5.1": _effort_none_default(_GPT51_EFFORTS, _GPT51_EFFORTS, ALWAYS),
    "gpt-5.2": _effort_none_default(_NONE_TO_XHIGH, _NONE_TO_XHIGH, ALWAYS),
    "gpt-5.2-pro": _pro(),
    "gpt-5.3-codex": _responses_only(
        efforts=_NONE_TO_XHIGH, verbosity=True, note="Coding model; Responses only."
    ),
    "gpt-5.4": _effort_none_default(_NONE_TO_XHIGH, _NONE_TO_XHIGH, EFFORT_NONE),
    "gpt-5.4-mini": _effort_none_default(_NONE_TO_XHIGH, _NONE_TO_XHIGH, EFFORT_NONE),
    "gpt-5.4-nano": _effort_none_default(_NONE_TO_XHIGH, _NONE_TO_XHIGH, EFFORT_NONE),
    "gpt-5.4-pro": _pro(),
    "gpt-5.5": dict(
        _effort_none_default(_NONE_TO_XHIGH, _NONE_TO_XHIGH, EFFORT_NONE),
        default_effort="medium",
        cc_default_effort="none",
    ),
    "gpt-5.5-pro": _pro(streaming=False),
    "gpt-5.6-luna": _gpt56_or_gpt6_none(),
    "gpt-5.6-sol": _gpt56_or_gpt6_none(),
    "gpt-5.6-terra": _gpt56_or_gpt6_none(),
    # GPT-6
    "gpt-6-astra": _gpt6_reasoning_only(),
    "gpt-6-sol": _gpt56_or_gpt6_none(),
    "gpt-6-luna": _gpt56_or_gpt6_none(),
    "gpt-6.1-sol": _gpt6_reasoning_only(),
    "gpt-6.1-terra": _gpt56_or_gpt6_none(),
    "gpt-6.2-astra": _gpt6_reasoning_only(),
    "gpt-6.2-sol": _gpt6_reasoning_only(),
}

#: Non-chat models, matched in order. Each pattern is anchored to a family
#: name, never to a bare ``gpt-`` prefix.
NONCHAT_PATTERNS = (
    (re.compile(r"^(text-embedding-|.*-embedding-)"), KIND_EMBEDDING),
    (re.compile(r"(^whisper-|transcribe|^gpt-realtime-whisper$)"), KIND_TRANSCRIPTION),
    (re.compile(r"(^tts-|-tts($|-))"), KIND_SPEECH),
    (re.compile(r"^(gpt-image-|chatgpt-image-|dall-e-)"), KIND_IMAGE),
    (re.compile(r"^(gpt-realtime|gpt-live-|gpt-4o(-mini)?-realtime)"), KIND_REALTIME),
    (re.compile(r"^(gpt-audio|gpt-4o(-mini)?-audio)"), KIND_AUDIO_CHAT),
    (re.compile(r"^(omni-moderation|text-moderation)"), KIND_MODERATION),
    (re.compile(r"(-search-api|-search-preview)$"), KIND_SEARCH),
    (re.compile(r"^(babbage-|davinci-|gpt-3\.5-turbo-instruct)"), KIND_COMPLETION),
)

#: Future members of known families. Inference is deliberately narrow: chat
#: on Responses only, function tools, no vision and no optional parameters
#: until the probe confirms more.
FAMILY_PATTERNS = (
    (re.compile(r"^gpt-(?:[6-9]|\d{2,})(?:\.\d+)?-[a-z]+(?P<pro>-pro)?$"), "GPT-6+"),
    (re.compile(r"^gpt-5\.\d+(?:-(?:mini|nano|luna|sol|terra|codex))?(?P<pro>-pro)?$"), "GPT-5.x"),
    (re.compile(r"^o\d+(?:-mini)?(?P<pro>-pro)?$"), "o-series"),
)

_SNAPSHOT_RE = re.compile(r"^(?P<base>.+?)-(?:\d{4}-\d{2}-\d{2}|\d{4})$")
_FINE_TUNE_RE = re.compile(r"^ft:(?P<base>[^:]+)")


def base_model_id(model_id):
    """Strip fine-tune and snapshot decorations: ``gpt-4o-2024-08-06`` → ``gpt-4o``."""
    name = (model_id or "").strip().lower()
    fine_tune = _FINE_TUNE_RE.match(name)
    if fine_tune:
        name = fine_tune.group("base")
    if name in CHAT_MODELS:
        return name
    snapshot = _SNAPSHOT_RE.match(name)
    if snapshot:
        return snapshot.group("base")
    return name


def _nonchat_kind(name):
    for pattern, kind in NONCHAT_PATTERNS:
        if pattern.search(name):
            return kind
    return None


def lookup(model_id):
    """Return :class:`OpenAIModelCapabilities` for an OpenAI model id."""
    model_id = (model_id or "").strip()
    base = base_model_id(model_id)
    if base in CHAT_MODELS:
        return OpenAIModelCapabilities(
            model_id=model_id, base_id=base, source=SOURCE_REGISTRY, **CHAT_MODELS[base]
        )
    kind = _nonchat_kind(base)
    if kind == KIND_AUDIO_CHAT:
        # OpenAI documents input_audio on Chat Completions only; Responses
        # rejects audio. Offer tools when the model family historically did.
        return OpenAIModelCapabilities(
            model_id=model_id,
            base_id=base,
            kind=KIND_AUDIO_CHAT,
            source=SOURCE_REGISTRY,
            endpoints=(CHAT_COMPLETIONS,),
            vision=False,
            function_tools=False,
            cc_function_tools=ALWAYS,
            streaming=True,
            note="Audio chat uses Chat Completions input_audio; not Responses.",
        )
    if kind:
        return OpenAIModelCapabilities(
            model_id=model_id, base_id=base, kind=kind, source=SOURCE_REGISTRY
        )
    for pattern, family in FAMILY_PATTERNS:
        match = pattern.match(base)
        if match:
            pro = bool(match.group("pro"))
            return OpenAIModelCapabilities(
                model_id=model_id,
                base_id=base,
                kind=KIND_CHAT,
                source=SOURCE_FAMILY,
                endpoints=(RESPONSES,),
                vision=False,
                function_tools=True,
                streaming=not pro,
                reasoning=True,
                note=(
                    "Not in the verified registry; inferred from the %s family. "
                    "Run 'Probe OpenAI Capabilities' to verify." % family
                ),
            )
    return OpenAIModelCapabilities(
        model_id=model_id,
        base_id=base,
        kind=KIND_UNKNOWN,
        source=SOURCE_UNKNOWN,
        note="Unknown OpenAI model. Run 'Probe OpenAI Capabilities' to verify.",
    )


def apply_probe(capabilities, probe):
    """Overlay a recorded probe result on an unverified capability entry.

    Registry entries are already verified and stay as they are; the probe is
    kept on the record as a diagnostic. Family and unknown entries take the
    endpoints, tool and vision results the provider actually accepted.
    """
    if not probe or capabilities.source == SOURCE_REGISTRY:
        return capabilities
    if capabilities.kind not in (KIND_CHAT, KIND_UNKNOWN):
        return capabilities
    results = probe.get("endpoints") or {}
    endpoints = tuple(
        endpoint for endpoint in ENDPOINTS if (results.get(endpoint) or {}).get("text")
    )
    responses = results.get(RESPONSES) or {}
    chat = results.get(CHAT_COMPLETIONS) or {}
    return replace(
        capabilities,
        kind=KIND_CHAT if endpoints else capabilities.kind,
        source=SOURCE_PROBE if endpoints else capabilities.source,
        endpoints=endpoints,
        function_tools=bool(responses.get("tools")),
        cc_function_tools=ALWAYS if chat.get("tools") else NEVER,
        vision=bool(responses.get("image") or chat.get("image")),
        sampling=ALWAYS if probe.get("sampling") else NEVER,
        streaming=bool(probe.get("streaming", capabilities.streaming)),
        reasoning=bool(probe.get("reasoning", capabilities.reasoning)),
        note="Capabilities recorded by the provider probe on %s." % (
            probe.get("checked_at") or "an unknown date"
        ),
    )


def summary(capabilities):
    """One readable line for the model form."""
    if not capabilities.chat_capable:
        label = KIND_LABELS.get(capabilities.kind, capabilities.kind)
        text = "Not a chat model (%s)." % label
        return "%s %s" % (text, capabilities.note) if capabilities.note else text
    parts = [
        "Endpoints: %s" % ", ".join(ENDPOINT_PATHS[e] for e in capabilities.endpoints),
        "vision" if capabilities.vision else "text only",
    ]
    tools = []
    if capabilities.function_tools and RESPONSES in capabilities.endpoints:
        tools.append("Responses")
    if CHAT_COMPLETIONS in capabilities.endpoints:
        if capabilities.cc_function_tools == ALWAYS:
            tools.append("Chat Completions")
        elif capabilities.cc_function_tools == EFFORT_NONE:
            tools.append("Chat Completions with reasoning effort none")
    parts.append("tools: %s" % (", ".join(tools) if tools else "none"))
    if capabilities.reasoning:
        efforts = capabilities.efforts
        parts.append(
            "reasoning effort: %s" % (", ".join(efforts) if efforts else "not validated")
        )
    if not capabilities.streaming:
        parts.append("no streaming")
    text = "; ".join(parts) + "."
    if capabilities.note:
        text += " " + capabilities.note
    return text
