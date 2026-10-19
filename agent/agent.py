import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List

from dotenv import load_dotenv
from openai import AsyncOpenAI

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobExecutorType,
    JobProcess,
    WorkerOptions,
    cli,
    room_io,
)

try:
    from livekit.agents.llm import StopResponse
except ImportError:
    from livekit.agents import StopResponse

from livekit.plugins import (
    deepgram,
    openai,
    silero,
)
try:
    from config import (
        ASSESSMENT_DURATION_SECONDS,
        WARNING_SECONDS,
        WARNING_DELAY_SECONDS,
        CLOSING_MESSAGE_PAUSE_SECONDS,
        OPENAI_MODEL,
        BEHAVIORAL_AREAS,
        SYSTEM_PROMPT,
        FINAL_ANALYSIS_PROMPT,
    )
except ImportError:
    from agent.config import (
        ASSESSMENT_DURATION_SECONDS,
        WARNING_SECONDS,
        WARNING_DELAY_SECONDS,
        CLOSING_MESSAGE_PAUSE_SECONDS,
        OPENAI_MODEL,
        BEHAVIORAL_AREAS,
        SYSTEM_PROMPT,
        FINAL_ANALYSIS_PROMPT,
    )

# ============================================================
# ENVIRONMENT
# ============================================================

# Load environment variables from the most likely locations.
# The project can keep .env in:
#   C:\\Behavioral-AI-Agent\\.env
# or .env/.env.local inside the agent folder.
#
# We intentionally do NOT print any secret values.

BASE_DIR = Path(__file__).resolve().parent

ENV_FILES = [
    BASE_DIR / ".env.local",
    BASE_DIR / ".env",
    BASE_DIR.parent / ".env",
    Path("C:/Behavioral-AI-Agent/agent/.env.local"),
    Path("C:/Behavioral-AI-Agent/.env"),
]

def sanitize_environment():
    """Sanitize environment variables by stripping whitespace, newlines, and surrounding quotes."""
    target_keys = (
        "OPENAI_API_KEY",
        "DEEPGRAM_API_KEY",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_URL",
        "OPENAI_MODEL",
        "ASSESSMENT_SYNC_SECRET",
        "BACKEND_URL",
    )
    for key in target_keys:
        val = os.environ.get(key)
        if val is not None:
            cleaned = val.strip().replace("\r", "").replace("\n", "").strip()
            if (cleaned.startswith('"') and cleaned.endswith('"')) or (cleaned.startswith("'") and cleaned.endswith("'")):
                cleaned = cleaned[1:-1].strip()
            os.environ[key] = cleaned

def get_clean_env(key: str) -> str | None:
    val = os.getenv(key)
    if not val:
        return None
    val = val.strip().replace("\r", "").replace("\n", "").strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val or None

_loaded_env_file = None

for _env_file in ENV_FILES:
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
        _loaded_env_file = _env_file
        break

sanitize_environment()

print(
    "Environment file:",
    str(_loaded_env_file) if _loaded_env_file else "NOT FOUND",
)

if not os.getenv("DEEPGRAM_API_KEY"):
    raise RuntimeError(
        "DEEPGRAM_API_KEY was not found in the environment."
    )

if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError(
        "OPENAI_API_KEY was not found in the environment."
    )

if not os.getenv("LIVEKIT_URL"):
    raise RuntimeError(
        "LIVEKIT_URL was not found in the environment."
    )

if not os.getenv("LIVEKIT_API_KEY"):
    raise RuntimeError(
        "LIVEKIT_API_KEY was not found in the environment."
    )

if not os.getenv("LIVEKIT_API_SECRET"):
    raise RuntimeError(
        "LIVEKIT_API_SECRET was not found in the environment."
    )

print("LiveKit URL loaded:", bool(os.getenv("LIVEKIT_URL")))
print("LiveKit API key loaded:", bool(os.getenv("LIVEKIT_API_KEY")))
print("LiveKit API secret loaded:", bool(os.getenv("LIVEKIT_API_SECRET")))
print("Deepgram API key loaded: True")
print("OpenAI API key loaded: True")

# Ensure Render health checks succeed whether hitting / or /health
from aiohttp import web
_orig_add_routes = web.Application.add_routes

def _patched_add_routes(self, routes):
    extra = []
    for route in routes:
        if getattr(route, "path", None) == "/":
            extra.append(web.get("/health", route.handler))
    return _orig_add_routes(self, list(routes) + extra)

web.Application.add_routes = _patched_add_routes


# ============================================================
# ASSESSMENT SETTINGS
# ============================================================


CLOSING_MESSAGE_PAUSE_SECONDS = 3

RESULTS_DIR = Path(__file__).resolve().parent / "assessments"


# ============================================================
# BEHAVIORAL AREAS
# ============================================================


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class BehavioralEvidence:
    area: str
    evidence: str
    strength: str = "unknown"
    confidence: float = 0.0


@dataclass
class AssessmentState:
    candidate_introduction: str = ""

    transcript: List[Dict[str, str]] = field(
        default_factory=list
    )

    evidence: Dict[str, List[BehavioralEvidence]] = field(
        default_factory=lambda: {
            area: []
            for area in BEHAVIORAL_AREAS
        }
    )

    questions_asked: int = 0
    completed: bool = False

    refused_questions: List[str] = field(
        default_factory=list
    )
    abusive_language_count: int = 0
    off_topic_count: int = 0
    is_paused: bool = False
    is_finalized: bool = False

    final_analysis: Dict = field(
        default_factory=dict
    )

    asked_questions: List[str] = field(
        default_factory=list
    )
    asked_areas: List[str] = field(
        default_factory=list
    )
    repeat_count_for_current_question: int = 0
    preferred_neutral_topic: str = ""
    neutral_topic_turns: int = 0
    frustration_count: int = 0
    waiting_for_resume: bool = False
    waiting_for_topic_choice: bool = False

    def add_transcript(
        self,
        role: str,
        text: str,
    ):
        self.transcript.append(
            {
                "role": role,
                "text": text,
            }
        )

    def add_evidence(
        self,
        area: str,
        evidence: str,
        strength: str = "unknown",
        confidence: float = 0.0,
    ):
        if area not in self.evidence:
            self.evidence[area] = []

        self.evidence[area].append(
            BehavioralEvidence(
                area=area,
                evidence=evidence,
                strength=strength,
                confidence=confidence,
            )
        )

    def to_dict(self):
        return {
            "candidate_introduction":
                self.candidate_introduction,

            "transcript":
                self.transcript,

            "behavioral_evidence": {
                area: [
                    {
                        "area": item.area,
                        "evidence": item.evidence,
                        "strength": item.strength,
                        "confidence": item.confidence,
                    }
                    for item in items
                ]
                for area, items in self.evidence.items()
            },

            "evidence": {
                area: [
                    {
                        "area": item.area,
                        "evidence": item.evidence,
                        "strength": item.strength,
                        "confidence": item.confidence,
                    }
                    for item in items
                ]
                for area, items in self.evidence.items()
            },

            "refused_questions":
                self.refused_questions,

            "abusive_language_count":
                self.abusive_language_count,

            "off_topic_count":
                self.off_topic_count,

            "questions_asked":
                self.questions_asked,

            "completed":
                self.completed,

            "is_paused":
                self.is_paused,

            "is_finalized":
                self.is_finalized,

            "final_analysis":
                self.final_analysis,

            "asked_questions":
                self.asked_questions,

            "asked_areas":
                self.asked_areas,

            "repeat_count_for_current_question":
                self.repeat_count_for_current_question,

            "preferred_neutral_topic":
                self.preferred_neutral_topic,

            "neutral_topic_turns":
                self.neutral_topic_turns,

            "frustration_count":
                self.frustration_count,

            "waiting_for_resume":
                self.waiting_for_resume,

            "waiting_for_topic_choice":
                self.waiting_for_topic_choice,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AssessmentState":
        """Restore AssessmentState from a saved dictionary, providing safe defaults for any missing fields."""
        if not data or not isinstance(data, dict):
            return cls()

        evidence_dict = {}
        raw_evidence = data.get("evidence") or data.get("behavioral_evidence") or {}
        if isinstance(raw_evidence, dict):
            for area in BEHAVIORAL_AREAS:
                items = raw_evidence.get(area, [])
                evidence_dict[area] = []
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            evidence_dict[area].append(
                                BehavioralEvidence(
                                    area=item.get("area", area),
                                    evidence=item.get("evidence", ""),
                                    strength=item.get("strength", "unknown"),
                                    confidence=float(item.get("confidence", 0.0)),
                                )
                            )

        state = cls(
            candidate_introduction=data.get("candidate_introduction", ""),
            transcript=list(data.get("transcript", [])),
            questions_asked=int(data.get("questions_asked", 0)),
            completed=bool(data.get("completed", False)),
            refused_questions=list(data.get("refused_questions", [])),
            abusive_language_count=int(data.get("abusive_language_count", 0)),
            off_topic_count=int(data.get("off_topic_count", 0)),
            is_paused=bool(data.get("is_paused", False)),
            is_finalized=bool(data.get("is_finalized", False)),
            final_analysis=dict(data.get("final_analysis", {})),
            asked_questions=list(data.get("asked_questions", [])),
            asked_areas=list(data.get("asked_areas", [])),
            repeat_count_for_current_question=int(data.get("repeat_count_for_current_question", 0)),
            preferred_neutral_topic=str(data.get("preferred_neutral_topic", "")),
            neutral_topic_turns=int(data.get("neutral_topic_turns", 0)),
            frustration_count=int(data.get("frustration_count", 0)),
            waiting_for_resume=bool(data.get("waiting_for_resume", False)),
            waiting_for_topic_choice=bool(data.get("waiting_for_topic_choice", False)),
        )
        if evidence_dict:
            state.evidence = evidence_dict
        return state


# ============================================================
# OPENAI CLIENT
# ============================================================

openai_client = AsyncOpenAI(
    api_key=get_clean_env("OPENAI_API_KEY")
)


# ============================================================
# SAVE RESULT
# ============================================================

def save_assessment(
    state: AssessmentState,
    assessment_id: str,
):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    safe_id = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in assessment_id
    )

    path = RESULTS_DIR / f"{safe_id}.json"

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            state.to_dict(),
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(
        f"Assessment saved to: {path}"
    )


# ============================================================
# CANDIDATE REFUSAL HANDLING & DETECTION HELPERS
# ============================================================

REFUSAL_EXACT_PATTERNS = [
    # Explicit unwillingness / preference not to answer
    r"\b(?:i\s+)?(?:do\s*n['’]?t|do\s+not|would\s+rather\s+not|'d\s+rather\s+not|prefer\s+not\s+to|'d\s+prefer\s+not\s+to|refuse\s+to|cannot|can['’]?t|won['’]?t)\s+(?:want\s+to\s+|wish\s+to\s+)?(?:answer|respond\s+to)\b",
    r"\bnot\s+(?:going\s+to\s+)?answer\s+(?:that|this|the\s+question|it)\b",
    # Discomfort with answering / question / topic
    r"\b(?:not|un|don['’]?t\s+feel|do\s+not\s+feel)\s*comfortable\s+(?:answering|discussing|with\s+(?:this|that|the)\s+(?:question|topic))\b",
    r"\b(?:rather|prefer)\s+not\s+to\s+(?:talk\s+about|discuss|get\s+into)\s+(?:this|that)\b",
    r"\b(?:do\s*n['’]?t|do\s+not)\s+want\s+to\s+(?:talk\s+about|discuss|get\s+into)\s+(?:this|that|(?:the\s+)?(?:professional|work|job|career)(?:\s+life)?(?:\s+topic)?)\b",
    # Request to skip, move on, or change question / topic
    r"\b(?:can|could|would|may)\s+(?:you|we|i)\s+(?:please\s+)?(?:skip(?:\s+to)?|pass\s+on|move\s+(?:on\s+)?(?:to|from)|change)\s+(?:this|that|the|another|a\s+different|next|the\s+next|something\s+else)(?:\s+(?:question|topic|subject))?\b",
    r"\b(?:can|could|would)\s+(?:you\s+)?ask\s+(?:me\s+)?(?:something\s+else|a\s+different\s+question|another\s+question)\b",
    r"\b(?:let['’]?s|please)\s+(?:skip|move\s+(?:on\s+)?to\s+(?:another|a\s+different|the\s+next|something\s+else)\s*(?:question|topic)?|change\s+the\s+topic)\b",
    r"\b(?:pass|skip)\s+on\s+this\s+(?:question)?\b",
    r"\b(?:i['’]?d\s+like\s+to\s+|i\s+want\s+to\s+|i['’]?d\s+rather\s+)?(?:pass|skip)\s+(?:on\s+)?(?:this|that|it|to\s+the\s+next)(?:\s+(?:one|question))?\b",
    r"\bask\s+(?:me\s+)?(?:something\s+else|a\s+different\s+question)\b",
    r"\bnext\s+question(?:\s+please)?\b",
]

_COMPILED_REFUSAL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in REFUSAL_EXACT_PATTERNS]

def is_candidate_refusal(text: str) -> bool:
    """Deterministic detection for explicit candidate refusals, discomfort,
    and requests to skip or change a question.
    Does NOT classify ordinary answers or 'I don't know' as refusals.
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_REFUSAL_PATTERNS)


def is_meta_interviewer_prompt(text: str) -> bool:
    """Return True if the text is a system boundary or meta redirect rather than an interview question."""
    if not text:
        return False
    t = text.strip()
    return (
        t.startswith("I understand. This is a professional behavioral interview")
        or t.startswith("I understand your request. This interview is specifically designed")
        or t.startswith("I'm happy to continue when we can communicate respectfully")
        or t.startswith("I'm happy to continue the professional interview")
        or t.startswith("I'm here to conduct a professional behavioral interview")
        or t.startswith("I'm here to conduct a professional interview")
        or t.startswith("I'm unable to continue the interview while the conversation remains abusive")
        or t.startswith("Take your time.")
        or t.startswith("Take all the time you need.")
        or t.startswith("As a reminder, our interview is dedicated")
        or t.startswith("As a reminder, our assessment time is dedicated")
        or t.startswith("As a reminder, professional and respectful")
        or t.startswith("Please maintain a respectful and professional tone")
        or t.startswith("I apologize if I've been repetitive")
        or t.startswith("I apologize for any frustration")
        or t.startswith("Since we are unable to communicate respectfully")
        or t.startswith("I understand. We will conclude the interview here.")
    )


def get_last_interviewer_question(state: AssessmentState) -> str:
    """Return the text of the most recent substantive question asked by the interviewer."""
    for item in reversed(state.transcript):
        if item.get("role") in ("interviewer", "assistant", "agent"):
            text = (item.get("text") or "").strip()
            if not text or is_meta_interviewer_prompt(text):
                continue
            clean = strip_refusal_prefix(text)
            candidate_text = clean if clean else text
            if "?" in candidate_text or any(candidate_text.lower().startswith(w) for w in (
                "tell me", "describe", "explain", "how", "what", "why", "can you", "could you", "please tell", "please share", "share an example"
            )):
                return text
    if getattr(state, "asked_questions", None):
        return state.asked_questions[-1]
    for item in reversed(state.transcript):
        if item.get("role") in ("interviewer", "assistant", "agent"):
            text = (item.get("text") or "").strip()
            if text and not is_meta_interviewer_prompt(text):
                return text
    return ""


# ============================================================
# CANDIDATE INTERVIEW ENDING INTENT & AMBIGUITY HELPERS
# ============================================================

INTERVIEW_END_EXACT_PATTERNS = [
    # Explicit commands / requests to stop, end, conclude, or finish the interview/assessment/session
    r"\b(?:please\s+)?(?:stop|end|conclude|finish|terminate|wrap\s+up|close|quit|cancel)\s+(?:this|the|our)?\s*(?:(?:fucking|damn|f\*\*\*ing|stupid|bullshit|crappy)\s+)?(?:interview|assessment|session|meeting|call)\b",
    r"\b(?:can|could|would|may|should)\s+(?:we|you|i)\s+(?:please\s+)?(?:stop|end|conclude|finish|wrap\s+up)\s+(?:this|the|our)?\s*(?:(?:fucking|damn|f\*\*\*ing|stupid|bullshit|crappy)\s+)?(?:interview|assessment|session)\b",
    r"\b(?:let['’]?s|can\s+we|could\s+we|we\s+can|we\s+should|should\s+we|shall\s+we|please)\s+(?:stop|end|conclude|finish|wrap\s+up)(?:\s+(?:it|this))?\s*(?:now|here|today)\b",
    r"\b(?:let['’]?s|please|we\s+can|we\s+should)\s+conclude\s+now\b",
    r"\bstop\s+(?:this|the)?\s*(?:(?:fucking|damn|f\*\*\*ing|stupid|bullshit|crappy)\s+)?interview\b",
    r"\bplease\s+end\s+(?:this|the)\s+interview\b",
    r"\blet['’]?s\s+conclude\s+now\b",

    # Unwillingness to continue the interview / assessment
    r"\b(?:i\s+)?(?:do\s*n['’]?t|do\s+not|would\s+rather\s+not|'d\s+rather\s+not|prefer\s+not\s+to|cannot|can['’]?t|won['’]?t)\s+(?:want\s+to\s+|wish\s+to\s+)?continue(?:\s+(?:with\s+)?(?:this|the)?\s*(?:(?:fucking|damn|f\*\*\*ing|stupid|bullshit|crappy)\s+)?(?:interview|assessment|session|further|anymore))?\b",
    r"\b(?:i['’]?m|i\s+am)\s+done\s+with\s+(?:this|the)\s+(?:interview|assessment)\b",

    # Explicit desire to stop / end / conclude here or now
    r"\b(?:i['’]?d\s+like\s+to|i\s+want\s+to|i\s+would\s+like\s+to|i\s+wish\s+to)\s+(?:stop|end|conclude|wrap\s+up)\s+(?:here|now)\b",
    r"\b(?:i['’]?d\s+like\s+to|i\s+want\s+to|i\s+would\s+like\s+to|i\s+wish\s+to)\s+(?:stop|end|conclude|finish|wrap\s+up)\s+(?:the|this|our)?\s*(?:(?:fucking|damn|f\*\*\*ing|stupid|bullshit|crappy)\s+)?(?:interview|assessment|session)\b",
    r"\b(?:i\s+)?want\s+to\s+stop\s+here\b",

    # "Please finish and give me the conclusion / results"
    r"\b(?:please\s+)?(?:finish|conclude|end)\s+(?:and\s+)?(?:give\s+me|show\s+me|provide)\s+(?:the\s+)?(?:conclusion|results?|feedback|report|assessment)\b",

    # "I don't want to do this anymore" / "exit the interview"
    r"\b(?:i\s+)?(?:do\s*n['’]?t|do\s+not)\s+want\s+to\s+do\s+this\s+anymore\b",
    r"\b(?:i\s+want\s+to\s+|can\s+i\s+)?exit\s+(?:the|this)\s+(?:(?:fucking|damn|f\*\*\*ing|stupid|bullshit|crappy)\s+)?(?:interview|assessment)\b",
]

_COMPILED_END_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INTERVIEW_END_EXACT_PATTERNS]

AMBIGUOUS_END_PATTERNS = [
    r"^(?:can|could)\s+(?:we|i)\s+stop(?:\s+now)?[?.!]?$",
    r"^(?:are|am)\s+(?:we|i)\s+done[?.!]?$",
    r"^is\s+(?:this|it)\s+(?:done|over|finished)[?.!]?$",
    r"^(?:should|can|could)\s+we\s+finish(?:\s+now)?[?.!]?$",
    r"^that['’]?s\s+(?:all|it)[?.!]?$",
]

_COMPILED_AMBIGUOUS_END = [re.compile(p, re.IGNORECASE) for p in AMBIGUOUS_END_PATTERNS]

CANDIDATE_END_CLOSING_MESSAGE = (
    "Understood. We will conclude the interview here and finalize your assessment. "
    "Thank you for your time."
)

AMBIGUOUS_END_CLARIFICATION_MESSAGE = (
    "Would you like to end the interview now, or would you like to continue with the next question?"
)


def is_candidate_end_request(text: str) -> bool:
    """Detect explicit candidate intent to stop, end, or conclude the interview.
    Distinguishes explicit ending requests from substantive answers discussing past work
    (e.g. 'I stopped the server', 'We finished the project').
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_END_PATTERNS)


def is_ambiguous_end_request(text: str) -> bool:
    """Detect short candidate questions that are ambiguous regarding ending the interview."""
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    if is_candidate_end_request(cleaned):
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_AMBIGUOUS_END)


PROFESSIONAL_REFUSAL_PREFIX = (
    "I understand. We can move to another relevant area. "
    "Please note that skipping a question may limit the evidence available for assessing this competency."
)

PROFESSIONAL_BOUNDARY_PREFIX = (
    "This interview focuses on evaluating your professional background, skills, and job-related experiences. "
    "Let's refocus on your workplace experience."
)

ABUSE_WARNING_FIRST_PREFIX = (
    "I'm here to conduct a professional behavioral interview. I understand that you may be frustrated, "
    "but I request that we communicate respectfully. We can continue with the interview when you're ready."
)

ABUSE_WARNING_REPEATED_PREFIX = (
    "I'm happy to continue the professional interview, but respectful communication is required. "
    "Please avoid abusive language so we can communicate respectfully and proceed professionally."
)

ABUSE_TERMINATION_MESSAGE = (
    "I'm unable to continue the interview while the conversation remains abusive. "
    "Since we are unable to communicate respectfully, we will conclude the interview here. "
    "Thank you for your time."
)

PROFESSIONAL_TOPIC_REQUEST_RESPONSE = (
    "I understand. This is a professional behavioral interview, so I'll keep our discussion focused on your work experience, skills, and workplace behavior. "
    "Would you like me to rephrase this question, or shall we move to another relevant interview question?"
)

PERSONAL_OR_UNRELATED_REQUEST_PATTERNS = [
    r"\b(?:can|could|would|may|let['’]?s|i\s+want\s+to|i['’]?d\s+like\s+to|shall\s+we)\s+(?:we|you|i)?\s*(?:please\s+)?(?:talk|chat|speak|discuss|focus)\s+(?:about\s+)?(?:my\s+|our\s+)?(?:personal\s+life|private\s+life|non-?work\s+topics?|something\s+outside\s+(?:of\s+)?work|things\s+unrelated\s+to\s+work)\b",
    r"\b(?:can|could|would|let['’]?s|i\s+want\s+to|i['’]?d\s+like\s+to)\s+(?:we|you|i)?\s*(?:talk|chat|speak|discuss)\s+(?:about\s+)?(?:movies|films|cinema|sports|weather|politics|dating|relationships|celebrities|video\s+games)\b",
    r"\b(?:can\s+we|let['’]?s)\s+(?:talk\s+(?:about\s+)?|discuss\s+)(?:something|topics?)\s+(?:outside\s+(?:of\s+)?work|unrelated\s+to\s+work|not\s+related\s+to\s+(?:work|the\s+job))\b",
    r"\bwhat\s+do\s+you\s+(?:like\s+to\s+)?do\s+on\s+(?:the\s+)?weekends?\b",
    r"\btell\s+me\s+about\s+your\s+(?:personal\s+life|hobbies|weekend)\b",
    r"\bwhat\s+are\s+your\s+(?:personal\s+)?hobbies\b",
    r"\b(?:are\s+you\s+(?:married|single|dating|human|a\s+robot|a\s+real\s+person|an\s+ai))\b",
    r"\b(?:where\s+do\s+you\s+live|where\s+are\s+you\s+(?:from|located)|how\s+old\s+are\s+you)\b",
    r"\b(?:what\s+is|what's)\s+your\s+(?:favorite|favourite|personal)\s+(?:movie|film|song|food|color|hobby|sport|actor|game)\b",
    r"\b(?:do\s+you\s+(?:have|like|watch|play))\s+(?:hobbies|movies|sports|games|football|cricket|video\s+games)\b",
]

ABUSIVE_LANGUAGE_PATTERNS = [
    r"\b(?:fuck|fucking|f\*\*\*)\s+(?:you|off|u)\b",
    r"\b(?:go\s+fuck\s+yourself|go\s+to\s+hell|eat\s+shit)\b",
    r"\b(?:shut\s+the\s+fuck\s+up|stfu)\b",
    r"\b(?:you\s+are|you're|u\s+r|you)\s+(?:an?\s+)?(?:stupid|idiot|moron|dumbass|dumb\s+(?:bot|ai|interviewer)|worthless|bitch|asshole|bastard|piece\s+of\s+shit|garbage)\b",
    r"\b(?:stupid|idiot|moron|dumb)\s+(?:bot|ai|interviewer)\b",
    r"\b(?:you\s+suck|you're\s+useless|you\s+are\s+useless)\b",
    r"\b(?:i\s+will|i'm\s+going\s+to|i['’]?ll)\s+(?:kill|hurt|destroy|break|punch|attack|smash)\s+(?:you|this\s+machine)\b",
]

WORKPLACE_REPORTED_SPEECH_PATTERNS = [
    r"\b(?:customer|client|caller|user|coworker|colleague|stakeholder|manager|boss|patient|patron|guest|passenger|employee|vendor)\s+(?:was|became|got|started|kept|were)?\s*(?:abusive|aggressive|hostile|angry|irate|rude|furious|screaming|shouting|yelling)\b",
    r"\b(?:customer|client|caller|user|coworker|colleague|stakeholder|manager|boss|patient|someone|person|they|he|she)\s+(?:shouted|yelled|screamed|said|told|called|messaged|emailed|complained|interrupted)\b",
    r"\b(?:called\s+(?:me|us|them|our\s+team|the\s+team|my\s+colleague|a\s+colleague|the\s+staff|an\s+agent)\s+(?:names|abusive|an?\s+\w+))\b",
    r"\b(?:dealing\s+with|handled?|de-?escalat(?:e|ed|ing)|resolv(?:e|ed|ing))\s+(?:an?\s+)?(?:difficult|abusive|irate|hostile|angry)\s+(?:customer|client|caller|user|situation|incident)\b",
    r"\b(?:abusive|hostile|aggressive|disrespectful)\s+(?:customer|client|caller|user|behavior|behaviour|language|call|incident)\b",
]

WORKPLACE_NARRATIVE_FRAME_PATTERNS = [
    r"\b(?:in|at|during)\s+(?:my|our)\s+(?:previous|past|last|former|prior)\s+(?:role|job|company|position|experience|project|workplace|team)\b",
    r"\b(?:when\s+i\s+(?:was\s+)?(?:working|worked|was\s+a|was\s+at|led|managed|supported))\b",
    r"\b(?:at|for)\s+(?:my|our)\s+(?:previous|last|former)\s+(?:company|employer|firm|agency)\b",
    r"\b(?:there\s+was\s+(?:a|an)\s+(?:incident|situation|time|case|escalation|occasion)\s+(?:where|when|involving))\b",
    r"\b(?:for\s+example|for\s+instance|in\s+one\s+(?:instance|case|scenario|situation))\b",
]

WORKPLACE_DEESCALATION_RESOLUTION_PATTERNS = [
    r"\b(?:de-?escalat(?:ed?|ing)|diffus(?:ed?|ing)|defus(?:ed?|ing))\b",
    r"\b(?:remained|stayed|kept)\s+(?:calm|professional|composed|patient|objective)\b",
    r"\b(?:resolv(?:ed?|ing)|handled?|addressed?|settled?)\s+(?:the\s+)?(?:situation|incident|issue|conflict|matter|complaint)\b",
    r"\b(?:empathiz(?:ed?|ing)|listened?\s+actively|customer\s+service|support\s+ticket)\b",
    r"\b(?:workplace|company|code\s+of\s+conduct|anti-?harassment|zero\s+tolerance)\s+policy\b",
]

DIRECT_BOT_ATTACK_PATTERNS = [
    r"\b(?:you|u)\s+(?:are|'re|r)?\s*(?:a|an)?\s*(?:stupid|dumb|idiot|moron|useless|worthless|terrible|garbage|piece\s+of\s+shit)\s+(?:bot|ai|interviewer|machine|system|agent)\b",
    r"\b(?:stupid|dumb|idiot|moron)\s+(?:bot|ai|interviewer|system)\b",
    r"\b(?:shut\s+the\s+fuck\s+up|fuck\s+off|go\s+fuck\s+yourself)\s+(?:you\s+)?(?:bot|ai|interviewer|machine)\b",
]

_COMPILED_PERSONAL = [re.compile(p, re.IGNORECASE) for p in PERSONAL_OR_UNRELATED_REQUEST_PATTERNS]
_COMPILED_ABUSE = [re.compile(p, re.IGNORECASE) for p in ABUSIVE_LANGUAGE_PATTERNS]
_COMPILED_WORKPLACE_REPORTED = [re.compile(p, re.IGNORECASE) for p in WORKPLACE_REPORTED_SPEECH_PATTERNS]
_COMPILED_WORKPLACE_FRAME = [re.compile(p, re.IGNORECASE) for p in WORKPLACE_NARRATIVE_FRAME_PATTERNS]
_COMPILED_WORKPLACE_RESOLUTION = [re.compile(p, re.IGNORECASE) for p in WORKPLACE_DEESCALATION_RESOLUTION_PATTERNS]
_COMPILED_DIRECT_BOT_ATTACK = [re.compile(p, re.IGNORECASE) for p in DIRECT_BOT_ATTACK_PATTERNS]


def is_personal_or_unrelated_request(text: str) -> bool:
    """Detect explicit requests by candidate to switch interview focus to personal life,
    hobbies, or unrelated conversational topics.
    Does NOT match substantive answers that mention personal background in support of a job competency.
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_PERSONAL)


def is_workplace_example_or_narrative_context(text: str) -> bool:
    """Detect if the candidate is describing a workplace incident, reported speech,
    or de-escalation example (e.g. dealing with an abusive customer or difficult coworker).
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    has_reported = any(p.search(cleaned) is not None for p in _COMPILED_WORKPLACE_REPORTED)
    has_frame = any(p.search(cleaned) is not None for p in _COMPILED_WORKPLACE_FRAME)
    has_resolution = any(p.search(cleaned) is not None for p in _COMPILED_WORKPLACE_RESOLUTION)
    return (
        (has_reported and (has_frame or has_resolution))
        or (has_reported and len(cleaned.split()) >= 8)
        or (has_frame and has_resolution)
    )


def is_direct_bot_attack(text: str) -> bool:
    """Detect explicit insults directly targeted at the AI interviewer/bot."""
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    return any(p.search(cleaned) is not None for p in _COMPILED_DIRECT_BOT_ATTACK)


def is_abusive_language(text: str) -> bool:
    """Conservative, context-aware detection for abusive, hostile, or insulting language directed at the interviewer.
    Does NOT flag ordinary frustration, technical disagreement, nervousness, polite refusals,
    or workplace examples discussing abusive customers/clients/coworkers.
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    if is_workplace_example_or_narrative_context(cleaned):
        if is_direct_bot_attack(cleaned):
            return True
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_ABUSE)


FRUSTRATION_PATTERNS = [
    r"\bare\s+you\s+(?:mad|crazy|insane|nuts|stupid|deaf|serious|kidding(?:\s+me)?)(?:\s+or\s+what)?[?.!]?\b",
    r"\bare\s+you\s+(?:even\s+)?(?:listening|hearing\s+me)\b",
    r"\b(?:why\s+do\s+you|why\s+are\s+you)\s+(?:keep\s+)?(?:asking|repeating)(?:\s+(?:me|this|that|the\s+same(?:\s+thing|\s+question)?))?\b",
    r"\b(?:stop\s+asking|you\s+already\s+asked|you['’]?ve\s+already\s+asked)\s+(?:me\s+)?(?:the\s+same(?:\s+thing|\s+question)?|that|this)\b",
    r"\b(?:you['’]?re|you\s+are)\s+not\s+(?:even\s+)?listening(?:\s+to\s+me)?\b",
]

_COMPILED_FRUSTRATION = [re.compile(p, re.IGNORECASE) for p in FRUSTRATION_PATTERNS]


def is_candidate_frustration(text: str) -> bool:
    """Detect candidate expressions of frustration, irritation, or repeated-question fatigue.
    Does NOT flag abusive language, profanity, or polite clarifications/refusals.
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_FRUSTRATION)


TOPIC_CHANGE_EXPLICIT_PATTERNS = [
    # Explicit unwillingness to discuss professional/work life and asking for another topic
    r"\b(?:can\s+we|could\s+we|we|let['’]?s|i\s+want\s+to|i['’]?d\s+like\s+to|shall\s+we)?\s*(?:talk|chat|speak|discuss)\s+(?:about\s+)?(?:some|a)?\s*(?:other|different|another)\s+topic\b",
    r"\b(?:can\s+we|could\s+we|we|let['’]?s|i\s+want\s+to|i['’]?d\s+like\s+to|shall\s+we)?\s*(?:talk|chat|speak|discuss)\s+(?:about\s+)?(?:something\s+else|different\s+things)\b",
    r"\b(?:can\s+we|could\s+we|let['’]?s|please|i\s+want\s+to|i['’]?d\s+like\s+to)?\s*(?:change|switch)\s+(?:the\s+)?topics?\b",
    r"\b(?:do\s*n['’]?t|do\s+not|would\s+rather\s+not|prefer\s+not\s+to)\s+(?:want\s+to\s+)?(?:talk\s+about|discuss)\s+(?:the\s+)?(?:professional|work|job|career)(?:\s+life)?(?:\s+topic)?\b",
    r"\b(?:change\s+the\s+topic|another\s+topic)\s+(?:because\s+)?(?:i\s+)?(?:do\s*n['’]?t|do\s+not|would\s+rather\s+not)\s+(?:want\s+to\s+)?(?:talk|discuss)\s+(?:about\s+)?(?:professional|work|job)\b",
    r"\b(?:i\s+)?(?:do\s*n['’]?t|do\s+not)\s+want\s+to\s+answer\s+(?:this|that|the)\s+question\b",
    r"\blet['’]?s\s+talk\s+about\s+something\s+else\b",
]

_COMPILED_TOPIC_CHANGE = [re.compile(p, re.IGNORECASE) for p in TOPIC_CHANGE_EXPLICIT_PATTERNS]


def is_explicit_topic_change_preference(text: str) -> bool:
    """Detect when a candidate explicitly asks to change away from professional/work topics."""
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_TOPIC_CHANGE)


CHOICE_MOVE_TO_ANOTHER_QUESTION_PATTERNS = [
    r"\b(?:move\s+(?:on\s+)?to\s+(?:another|a\s+different|the\s+next|a\s+new)|another|a\s+different|next)\s+(?:relevant\s+)?(?:interview\s+)?question\b",
    r"\b(?:let['’]?s\s+)?(?:move\s+to\s+another|ask\s+another|try\s+another|another\s+question|different\s+question|next\s+question)\b",
    r"\b(?:i['’]?d\s+prefer\s+(?:to\s+)?move\s+to|prefer\s+another\s+question|move\s+to\s+another|another\s+relevant\s+question)\b",
    r"\b(?:the\s+)?(?:second\s+option|latter|another\s+question|next\s+one)\b",
    r"\bmove\s+to\s+another\b",
    r"\b(?:let['’]?s\s+)?move\s+on\b",
]

_COMPILED_CHOICE_MOVE = [re.compile(p, re.IGNORECASE) for p in CHOICE_MOVE_TO_ANOTHER_QUESTION_PATTERNS]


def is_choice_to_move_to_another_question(text: str) -> bool:
    """Detect candidate choice to move to another relevant professional interview question."""
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_CHOICE_MOVE)


PERMISSION_TO_RESUME_PATTERNS = [
    r"^(?:yes|sure|okay|ok|fine)[,.\s]+(?:i['’]?m|i\s+am)?\s*ready[.!]?$",
    r"^(?:yes|sure)[,.\s]+(?:let['’]?s|we\s+can)\s+(?:continue|resume|proceed)[.!]?$",
    r"\b(?:i['’]?m|i\s+am)\s+ready\b",
    r"\bready\s+to\s+(?:continue|resume|proceed|start)\b",
    r"^ready[.!]?$",
    r"\b(?:we\s+can|let['’]?s)\s+(?:return|continue|resume|get\s+back|proceed)(?:\s+to\s+(?:the\s+)?(?:interview|assessment|questions))?\b",
    r"\b(?:go\s+ahead|we\s+can\s+continue|let['’]?s\s+do\s+it|please\s+continue|please\s+resume)\b",
    r"^(?:yes|sure|yeah|yep|yes\s+please)[.!]?$",
    r"\b(?:i\s+)?(?:want\s+to|would\s+like\s+to)\s+continue\b",
]

_COMPILED_PERMISSION = [re.compile(p, re.IGNORECASE) for p in PERMISSION_TO_RESUME_PATTERNS]

BARE_ACKNOWLEDGMENT_PATTERNS = [
    r"^(?:okay|ok|alright|fine|sure|got\s+it|understood|i\s+see)[.!]?$",
]
_COMPILED_BARE_ACK = [re.compile(p, re.IGNORECASE) for p in BARE_ACKNOWLEDGMENT_PATTERNS]


def is_bare_acknowledgment(text: str) -> bool:
    """Return True if text is simply a bare acknowledgment like 'Okay', 'Ok', 'Alright'
    without clear agreement to resume.
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    return any(p.search(cleaned) is not None for p in _COMPILED_BARE_ACK)


def is_permission_to_resume(text: str) -> bool:
    """Detect candidate consent or willingness to resume the assessment after a neutral topic break.
    Does NOT treat a bare 'Okay' or 'Ok' as permission to resume when it could simply acknowledge the previous response.
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if is_bare_acknowledgment(cleaned):
        return False
    return any(p.search(cleaned) is not None for p in _COMPILED_PERMISSION)


def extract_neutral_topic(text: str) -> str:
    """Extract a preferred neutral topic mentioned by the candidate (e.g. movies, action movies, sports, gaming)."""
    if not text:
        return ""
    lower = text.lower()
    if "action movie" in lower or "action film" in lower:
        return "action movies"
    if any(k in lower for k in ("movie", "film", "cinema")):
        return "movies"
    if any(k in lower for k in ("video game", "gaming", "playstation", "xbox")):
        return "video games"
    if any(k in lower for k in ("sport", "football", "soccer", "basketball", "cricket", "tennis")):
        return "sports"
    if any(k in lower for k in ("music", "song", "band")):
        return "music"
    return ""


# Off-topic patterns detecting non-work entertainment, cinema, games, sports, or weather
OFF_TOPIC_PATTERNS = [
    # Movies, cinema, films, actors, Hollywood, streaming entertainment
    r"\b(?:watch|watching|watched|love|likes?|favorite|favourite|enjoy|saw|see|seen)\s+(?:a\s+lot\s+of\s+|many\s+|some\s+)?(?:movies?|films?|cinema|hollywood|netflix|tv\s+shows?|series|marvel|avengers|batman|superman|disney|anime|action\s+movies?|sci-?fi\s+movies?|thrillers?)\b",
    r"\b(?:a\s+)?(?:movies?|films?|cinema)\s+and\s+(?:other\s+things|stuff)\b",
    r"\b(?:action\s+movies?|sci-?fi\s+movies?|thriller\s+movies?|horror\s+movies?)\b",
    r"\b(?:i\s+said\s+|i\s+told\s+you\s+|i\s+already\s+said\s+)?(?:i\s+)?(?:love|like|enjoy|watch|am\s+into)\s+(?:watching\s+)?(?:marvel\s+)?(?:movies?|films?|cinema)\b",
    r"\b(?:my\s+favorite\s+(?:movie|film|actor|actress|director|tv\s+show|series)|favorite\s+movie\s+is|favorite\s+film\s+is|favourite\s+movie\s+is)\b",
    r"\b(?:movie|film|cinema)\s+(?:theater|theatre|tickets?|popcorn|acting|actors?|actress|director|night|nights)\b",
    r"\b(?:in\s+the\s+movie|in\s+that\s+film|the\s+movie\s+was|the\s+film\s+was)\b",
    r"\b(?:watching\s+films?|watching\s+movies?)\b",
    r"\b(?:movies?|films?)\s+(?:are|is)\s+(?:my\s+favorite|great|awesome|fun|cool|what\s+i\s+like)\b",
    r"\b(?:marvel\s+movies?|avengers(?:\s+endgame)?|inception|titanic|spider-?man|the\s+godfather|star\s+wars)\b",
    # Video games, gaming
    r"\b(?:play|playing|played)\s+(?:video\s+games?|computer\s+games?|playstation|xbox|nintendo|fortnite|call\s+of\s+duty|minecraft|gta|league\s+of\s+legends|valorant|fifa)\b",
    r"\b(?:my\s+favorite\s+(?:game|video\s+game)|favorite\s+game\s+is)\b",
    # Sports matches, fandom
    r"\b(?:watch|watching|watched|see|saw|catch|caught|follow|fan\s+of|love)\s+(?:the\s+)?(?:football|soccer|cricket|basketball|nba|nfl|baseball|tennis)\s+(?:game|match|tournament|league|cup|scores?|highlights?|team)\b",
    r"\b(?:did\s+you\s+(?:see|watch|catch)|who\s+won)\s+(?:the\s+)?(?:game|match|championship|super\s*bowl|world\s*cup|football|soccer|cricket|basketball)\b",
    # Weather banter
    r"\b(?:the\s+weather\s+is|it['’]?s\s+(?:so\s+)?(?:sunny|rainy|raining|cold|hot|snowing|humid)\s+outside|nice\s+weather|bad\s+weather)\b",
    # Celebrity gossip, dating, nightlife
    r"\b(?:my\s+(?:boyfriend|girlfriend|partner|date)\s+and\s+i|went\s+on\s+a\s+date|dating|tinder|party\s+last\s+night|clubbing|hungover)\b",
]

_COMPILED_OFF_TOPIC = [re.compile(p, re.IGNORECASE) for p in OFF_TOPIC_PATTERNS]

WORKPLACE_INDICATORS = {
    "project", "team", "engineer", "engineering", "developer", "development",
    "work", "worked", "working", "job", "company", "client", "stakeholder",
    "code", "coding", "software", "system", "architecture", "database", "api",
    "manager", "leadership", "production", "incident", "deadline", "bug",
    "feature", "resolved", "solved", "deliver", "delivered", "responsibility",
    "responsible", "career", "experience", "lead", "mentor", "mentored",
    "collaborate", "collaborated", "communication", "challenge", "challenging",
    "role", "backend", "frontend", "fullstack", "devops", "cloud", "aws", "gcp",
    "azure", "docker", "kubernetes", "git", "python", "javascript", "react", "node",
    "sql", "nosql", "linux", "testing", "unit test", "design", "designed",
}

CLARIFICATION_PATTERNS = [
    r"\b(?:can|could|would)\s+you\s+(?:please\s+)?(?:repeat|clarify|rephrase|say\s+that\s+again|explain)\b",
    r"\b(?:what\s+do\s+you\s+mean|what\s+does\s+that\s+mean)\b",
    r"\b(?:pardon|excuse\s+me|come\s+again|didn['’]?t\s+(?:hear|catch)\s+that)\b",
    r"\brepeat\s+the\s+question\b",
    r"\b(?:please\s+)?(?:repeat|clarify|rephrase)(?:\s+(?:the|that|this)(?:\s+question)?)?\b",
    r"\b(?:could\s+you|can\s+you|please)?\s*(?:say\s+that\s+again|say\s+it\s+again)\b",
]
_COMPILED_CLARIFICATION = [re.compile(p, re.IGNORECASE) for p in CLARIFICATION_PATTERNS]

NERVOUSNESS_PATTERNS = [
    r"\b(?:i\s+am|i'm|feeling)\s+(?:very\s+|a\s+bit\s+|a\s+little\s+|quite\s+)?(?:nervous|anxious|stressed)\b",
    r"\b(?:give\s+me\s+a\s+moment|let\s+me\s+think|just\s+thinking)\b",
]
_COMPILED_NERVOUSNESS = [re.compile(p, re.IGNORECASE) for p in NERVOUSNESS_PATTERNS]

DISAGREEMENT_PATTERNS = [
    r"\b(?:i\s+)?(?:disagree|disagreed)\b",
    r"\b(?:i\s+)?(?:don['’]?t|do\s+not)\s+agree\b",
]
_COMPILED_DISAGREEMENT = [re.compile(p, re.IGNORECASE) for p in DISAGREEMENT_PATTERNS]


def is_clarification_request(text: str) -> bool:
    """Detect reasonable candidate clarification requests (e.g. asking to repeat or explain)."""
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    return any(p.search(cleaned) is not None for p in _COMPILED_CLARIFICATION)


def is_nervousness_or_disagreement(text: str) -> bool:
    """Detect natural expressions of nervousness, anxiety, or technical disagreement."""
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    return (
        any(p.search(cleaned) is not None for p in _COMPILED_NERVOUSNESS)
        or any(p.search(cleaned) is not None for p in _COMPILED_DISAGREEMENT)
    )


def has_substantive_workplace_context(text: str) -> bool:
    """Return True if the text contains substantive workplace or technical keywords."""
    if not text:
        return False
    words = set(re.findall(r"\b[a-z]{3,}\b", text.lower()))
    matches = words & WORKPLACE_INDICATORS
    return len(matches) >= 2


def is_off_topic_answer(text: str) -> bool:
    """Detect when the candidate's response is unrelated to the interview question (e.g. movies, gaming, sports).
    Does NOT flag relevant personal examples, nervousness, disagreement, or clarification requests.
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False

    if is_clarification_request(cleaned):
        return False
    if is_nervousness_or_disagreement(cleaned):
        return False

    matched = any(p.search(cleaned) is not None for p in _COMPILED_OFF_TOPIC)
    if not matched:
        return False

    if has_substantive_workplace_context(cleaned):
        return False

    return True


def is_non_introduction_turn(text: str, state: AssessmentState | None = None) -> bool:
    """Return True if text is frustration, a topic-change request, a neutral-topic reply,
    a clarification request, a refusal, an ending request, abusive language, or off-topic,
    and therefore must NOT populate or overwrite candidate_introduction.
    """
    if not text or not isinstance(text, str):
        return True
    cleaned = text.strip()
    if not cleaned:
        return True
    if state is not None and (
        bool(getattr(state, "waiting_for_resume", False))
        or bool(getattr(state, "waiting_for_topic_choice", False))
        or bool(getattr(state, "preferred_neutral_topic", ""))
        or getattr(state, "neutral_topic_turns", 0) > 0
    ):
        return True
    return (
        is_candidate_frustration(cleaned)
        or is_explicit_topic_change_preference(cleaned)
        or is_bare_acknowledgment(cleaned)
        or is_permission_to_resume(cleaned)
        or is_choice_to_move_to_another_question(cleaned)
        or is_clarification_request(cleaned)
        or is_candidate_refusal(cleaned)
        or is_personal_or_unrelated_request(cleaned)
        or is_off_topic_answer(cleaned)
        or is_abusive_language(cleaned)
        or is_candidate_end_request(cleaned)
        or is_ambiguous_end_request(cleaned)
    )


OFF_TOPIC_FIRST_PREFIX = (
    "This assessment focuses on evaluating your professional background, skills, and workplace experiences. "
    "Let's refocus on your workplace experience."
)

OFF_TOPIC_REPEATED_PREFIX = (
    "As a reminder, our assessment time is dedicated to your professional background and qualifications. "
    "Let's stay focused on the interview questions."
)

UNRELATED_ENTERTAINMENT_QUESTION_PATTERNS = [
    r"\bmovie\s+nights?\b",
    r"\b(?:favorite|favourite)\s+(?:movie|film|actor|actress|director|song|band|album|celebrity|video\s+game|game|show|series)\b",
    r"\b(?:organize|organizing|host|hosting|plan|planning)\s+(?:a\s+)?(?:movie\s+night|film\s+screening|game\s+night)\b",
    r"\b(?:marvel|inception|avengers|hollywood|netflix\s+show|disney\s+plus)\b",
    r"\b(?:watch|watching)\s+(?:movies?|films?|tv\s+shows?|series)\b",
    r"\b(?:video\s+games?|gaming|playstation|xbox|nintendo)\b",
    r"\b(?:celebrities|celebrity|gossip)\b",
]
_COMPILED_UNRELATED_QUESTION = [re.compile(p, re.IGNORECASE) for p in UNRELATED_ENTERTAINMENT_QUESTION_PATTERNS]


def is_unrelated_entertainment_question(text: str) -> bool:
    """Return True if a question mentions movies, movie nights, entertainment preferences, gaming, or celebrities."""
    if not text or not isinstance(text, str):
        return False
    cleaned = text.strip().lower()
    return any(p.search(cleaned) is not None for p in _COMPILED_UNRELATED_QUESTION)


def is_repeated_off_topic(state: AssessmentState, current_text: str = "") -> bool:
    """Detect if an off-topic answer is repeated, checking state counter and prior candidate turns in transcript."""
    if getattr(state, "off_topic_count", 0) >= 1:
        return True
    transcript_items = getattr(state, "transcript", [])
    if transcript_items and transcript_items[-1].get("role") in ("candidate", "user") and transcript_items[-1].get("text") == current_text:
        prior_items = transcript_items[:-1]
    else:
        prior_items = transcript_items
    prior_off_topic_count = sum(
        1 for item in prior_items
        if item.get("role") in ("candidate", "user") and is_off_topic_answer(item.get("text", ""))
    )
    return prior_off_topic_count >= 1


def format_boundary_response(question_text: str) -> str:
    """Ensure an off-topic redirection begins with professional boundary explanation and refocusing."""
    q = (question_text or "").strip()
    if not q:
        return PROFESSIONAL_BOUNDARY_PREFIX
    if q.startswith(PROFESSIONAL_BOUNDARY_PREFIX):
        return q
    q_substantive = strip_refusal_prefix(q)
    return f"{PROFESSIONAL_BOUNDARY_PREFIX} {q_substantive}" if q_substantive else PROFESSIONAL_BOUNDARY_PREFIX


def format_abuse_warning_response(question_text: str, count: int) -> str:
    """Ensure an abusive turn response begins with calm warning and question redirect (1st) or repeated boundary offer (2nd+)."""
    if count <= 1:
        prefix = ABUSE_WARNING_FIRST_PREFIX
        q = (question_text or "").strip()
        if not q:
            return prefix
        if q.startswith(prefix):
            return q
        q_substantive = strip_refusal_prefix(q)
        return f"{prefix} {q_substantive}" if q_substantive else prefix
    else:
        return ABUSE_WARNING_REPEATED_PREFIX


def format_refusal_response(question_text: str) -> str:
    """Ensure a refusal redirection begins with a professional, respectful acknowledgment.
    Idempotent: will not duplicate prefix if already formatted or acknowledged.
    """
    q = (question_text or "").strip()
    if not q:
        return PROFESSIONAL_REFUSAL_PREFIX

    if q.startswith(PROFESSIONAL_REFUSAL_PREFIX):
        return q

    old_prefix = "I understand. Let's move to another relevant question."
    if q.startswith(old_prefix):
        remainder = q[len(old_prefix):].strip()
        return f"{PROFESSIONAL_REFUSAL_PREFIX} {remainder}" if remainder else PROFESSIONAL_REFUSAL_PREFIX

    ack_starters = (
        "i understand. we can move to another relevant area",
        "i understand. let's move to another relevant question",
        "i understand",
        "understood",
        "no problem",
        "no worries",
        "that's fine",
        "that is fine",
        "that's completely fine",
        "that is completely fine",
        "that's okay",
        "that is okay",
        "certainly",
        "sure",
        "of course",
    )
    if q.lower().startswith("i understand") and "skipping a question may limit" not in q.lower():
        rest = strip_refusal_prefix(q)
        return f"{PROFESSIONAL_REFUSAL_PREFIX} {rest}" if rest else PROFESSIONAL_REFUSAL_PREFIX

    if not any(q.lower().startswith(starter) for starter in ack_starters):
        return f"{PROFESSIONAL_REFUSAL_PREFIX} {q}"
    return q


def get_next_redirect_question(state: AssessmentState) -> str:
    """Select an unasked safe behavioral or neutral fallback question from another dimension to prevent loops."""
    asked_norm = {
        normalize_question(item.get("text", ""))
        for item in state.transcript
        if item.get("role") in ("interviewer", "assistant", "agent")
    }
    for q in getattr(state, "asked_questions", []):
        if q:
            asked_norm.add(normalize_question(q))
    refused_norm = {
        normalize_question(q)
        for q in getattr(state, "refused_questions", [])
    }
    excluded = asked_norm | refused_norm

    for fallback in FALLBACK_BEHAVIORAL_QUESTIONS:
        fb_norm = normalize_question(fallback)
        if (
            fb_norm not in excluded
            and not is_refused_topic_repetition(fallback, state.refused_questions)
            and not is_unrelated_entertainment_question(fallback)
        ):
            return fallback

    for safe_q in SAFE_NEUTRAL_QUESTIONS:
        sq_norm = normalize_question(safe_q)
        if (
            sq_norm not in excluded
            and not is_refused_topic_repetition(safe_q, state.refused_questions)
            and not is_unrelated_entertainment_question(safe_q)
        ):
            return safe_q

    return "Could you tell me about what motivated you to pursue your current career path?"


def strip_refusal_prefix(text: str) -> str:
    """Strip professional refusal acknowledgment, boundary, abuse warning, or off-topic phrases from the start of a question.
    Ensures question comparisons and topic analysis focus solely on the substantive question.
    """
    if not text:
        return ""
    q = text.strip()
    prefixes_to_check = (
        PROFESSIONAL_REFUSAL_PREFIX,
        PROFESSIONAL_BOUNDARY_PREFIX,
        ABUSE_WARNING_FIRST_PREFIX,
        ABUSE_WARNING_REPEATED_PREFIX,
        ABUSE_TERMINATION_MESSAGE,
        OFF_TOPIC_FIRST_PREFIX,
        OFF_TOPIC_REPEATED_PREFIX,
    )
    for pfx in prefixes_to_check:
        if q.lower().startswith(pfx.lower()):
            q = q[len(pfx):].strip()

    refusal_starters = (
        "please note that skipping a question may limit the evidence available for assessing this competency.",
        "we can move to another relevant area. please note that skipping a question may limit the evidence available for assessing this competency.",
        "this assessment focuses on evaluating your professional background, skills, and workplace experiences. let's refocus on your workplace experience.",
        "this assessment focuses on evaluating your professional background, skills, and workplace experiences.",
        "as a reminder, our assessment time is dedicated to your professional background and qualifications. let's stay focused on the interview questions.",
        "as a reminder, our assessment time is dedicated to your professional background and qualifications.",
        "let's stay focused on the interview questions.",
        "this interview focuses on evaluating your professional background, skills, and job-related experiences. let's refocus on your workplace experience.",
        "this interview focuses on evaluating your professional background, skills, and job-related experiences.",
        "let's refocus on your workplace experience.",
        "let's refocus on the discussion.",
        "please maintain a respectful and professional tone during this assessment. let's return to the interview.",
        "please maintain a respectful and professional tone during this assessment.",
        "let's return to the interview.",
        "as a reminder, professional and respectful communication is required throughout this assessment. let's refocus on the discussion.",
        "as a reminder, professional and respectful communication is required throughout this assessment.",
        "we can move to another relevant area.",
        "let's move to another relevant question.",
        "let's move to another question.",
        "let's move to a different question.",
        "i understand, that is completely fine.",
        "i understand, that's completely fine.",
        "i understand.",
        "understood.",
        "no problem.",
        "no worries.",
        "that's completely fine.",
        "that is completely fine.",
        "that's fine.",
        "that is fine.",
    )
    changed = True
    while changed:
        changed = False
        lower = q.lower()
        for starter in refusal_starters:
            if lower.startswith(starter):
                q = q[len(starter):].strip()
                changed = True
                break
    return q


def get_question_behavioral_area(question_text: str) -> str:
    """Identify the likely behavioral area of a question based on key indicators."""
    if not question_text or not isinstance(question_text, str):
        return ""
    lower = question_text.lower()
    if any(k in lower for k in ("collaborate", "team member", "coworker", "team", "conflict", "disagree", "disagreement", "perspective")):
        return "teamwork"
    if any(k in lower for k in ("mistake", "error", "setback", "ownership", "fail", "accountab")):
        return "accountability"
    if any(k in lower for k in ("priorities", "changed unexpectedly", "adapt", "flexible", "ambiguity", "stress")):
        return "adaptability"
    if any(k in lower for k in ("decision", "high-stakes", "reasoning", "trade-off", "tradeoff")):
        return "decision_making"
    if any(k in lower for k in ("lead", "leadership", "initiative", "guide colleagues", "milestone", "mentor")):
        return "leadership"
    if any(k in lower for k in ("communicat", "stakeholder", "persuaded", "cross-functional", "explain complex", "present")):
        return "communication"
    if any(k in lower for k in ("problem", "technical", "operational", "resolve", "troubleshoot", "debugging", "bug", "architecture")):
        return "problem_solving"
    return ""


def normalize_question(text: str) -> str:
    """Normalize a question by removing refusal prefixes, lowercasing, and stripping punctuation/whitespace."""
    if not text:
        return ""
    q = strip_refusal_prefix(text).strip().lower()
    return q.rstrip("?.! ")


# ============================================================
# REFUSED TOPIC REPETITION SAFEGUARD
# ============================================================

REFUSED_TOPIC_CLUSTERS: dict[str, set[str]] = {
    "conflict_disagreement": {
        "conflict", "conflicts", "disagree", "disagreement", "disagreements",
        "disagreed", "clash", "clashed", "dispute", "disputes", "argument",
        "arguments", "friction",
    },
    "mistake_failure": {
        "mistake", "mistakes", "failure", "failures", "fail", "failed",
        "error", "errors", "blunder", "blunders", "setback", "setbacks",
        "regret", "regretted", "regrets", "went wrong",
    },
    "criticism_negative_feedback": {
        "criticism", "critical feedback", "negative feedback", "reprimand",
        "reprimanded", "underperformance", "underperformed",
    },
    "termination_leaving": {
        "fired", "firing", "terminate", "terminated", "termination",
        "dismissed", "dismissal", "let go", "laid off", "layoff",
        "quit", "quitting", "resigned", "resignation",
    },
    "stress_burnout": {
        "burnout", "burned out", "overwhelmed", "overwhelming", "stress", "stressed",
    },
}

INTERVIEW_STOPWORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
    "which", "this", "that", "these", "those", "then", "just", "so", "than",
    "such", "both", "through", "about", "for", "is", "of", "while", "during",
    "to", "from", "in", "out", "on", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how", "all", "any",
    "both", "each", "few", "more", "most", "other", "some", "no", "nor", "not",
    "only", "own", "same", "so", "too", "very", "can", "will", "don", "should",
    "now", "i", "me", "my", "myself", "we", "our", "ours", "you", "your",
    "yours", "yourself", "yourselves", "he", "him", "his", "she", "her", "hers",
    "it", "its", "they", "them", "their", "theirs",
    "tell", "describe", "share", "give", "example", "situation", "time",
    "experience", "scenario", "explain", "talk", "discuss", "instance",
    "would", "could", "did", "were", "was", "have", "had", "handled",
    "approach", "steps", "take", "took", "action", "actions", "result",
    "outcome", "handle", "handling", "work", "worked", "working", "job",
    "role", "workplace", "project", "team", "colleague", "colleagues",
    "understand", "move", "relevant", "question", "another", "different",
}


def extract_content_tokens(text: str) -> set[str]:
    raw_tokens = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return {t for t in raw_tokens if t not in INTERVIEW_STOPWORDS}


def get_question_topics(text: str) -> set[str]:
    lower = text.lower()
    topics = set()
    for topic_name, keywords in REFUSED_TOPIC_CLUSTERS.items():
        if any(kw in lower for kw in keywords):
            topics.add(topic_name)
    return topics


def is_refused_topic_repetition(candidate_question: str, refused_questions: list[str]) -> bool:
    """Reliable safeguard checking whether a candidate question repeats a refused topic
    with different wording, without blocking unrelated questions.
    """
    if not candidate_question or not refused_questions:
        return False
    c_clean = strip_refusal_prefix(candidate_question).strip().lower()
    c_topics = get_question_topics(c_clean)
    c_tokens = extract_content_tokens(c_clean)

    for refused in refused_questions:
        r_clean = strip_refusal_prefix(refused or "").strip().lower()
        if not r_clean:
            continue
        # Exact or substantive substring match (ignoring refusal prefix and punctuation)
        r_norm = r_clean.rstrip("?.! ")
        c_norm = c_clean.rstrip("?.! ")
        if r_norm == c_norm or (len(r_norm) >= 15 and r_norm in c_norm) or (len(c_norm) >= 15 and c_norm in r_norm):
            return True
        # Thematic topic cluster match (e.g. conflict/disagreement or mistake/failure)
        r_topics = get_question_topics(r_clean)
        if c_topics and r_topics and bool(c_topics & r_topics):
            return True
        # Content keyword overlap (>= 2 non-generic content words)
        r_tokens = extract_content_tokens(r_clean)
        shared = c_tokens & r_tokens
        if len(shared) >= 2:
            return True
    return False


# ============================================================
# JSON EXTRACTION
# ============================================================

def extract_json(
    text: str,
):
    if not text:
        return {}

    text = text.strip()

    # Strip markdown code blocks (e.g. ```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1:
        try:
            return json.loads(
                text[start:end + 1]
            )
        except Exception:
            pass

    return {}


# ============================================================
# FORMAT EVIDENCE FOR LLM
# ============================================================
def format_transcript(
    state: AssessmentState,
    max_turns: int = 6,
) -> str:

    if not state.transcript:
        return "No recent conversation available."

    recent_items = state.transcript[-max_turns:]

    lines = []

    for item in recent_items:

        role = item.get(
            "role",
            "unknown",
        )

        text = item.get(
            "text",
            "",
        )

        lines.append(
            f"{role.upper()}: {text}"
        )

    return "\n".join(lines)


def format_evidence(
    state: AssessmentState,
    max_items_per_area: int = 2,
) -> str:

    lines = []

    for area in BEHAVIORAL_AREAS:

        items = state.evidence.get(
            area,
            [],
        )

        lines.append(
            f"\n{area.upper()}:"
        )

        if not items:
            lines.append(
                "No evidence collected."
            )
            continue

        recent_items = items[
            -max_items_per_area:
        ]

        for item in recent_items:

            lines.append(
                f"- Evidence: {item.evidence}"
            )

            lines.append(
                f"  Strength: {item.strength}"
            )

            lines.append(
                f"  Confidence: {item.confidence}"
            )

    return "\n".join(lines)

# ============================================================
# FULL HISTORY FOR FINAL ANALYSIS
# ============================================================
def format_full_transcript(
    state: AssessmentState,
) -> str:

    if not state.transcript:
        return "No conversation available."

    lines = []

    for item in state.transcript:

        role = item.get(
            "role",
            "unknown",
        )

        text = item.get(
            "text",
            "",
        )

        lines.append(
            f"{role.upper()}: {text}"
        )

    return "\n".join(lines)


def format_full_evidence(
    state: AssessmentState,
) -> str:

    lines = []

    for area in BEHAVIORAL_AREAS:

        items = state.evidence.get(
            area,
            [],
        )

        lines.append(
            f"\n{area.upper()}:"
        )

        if not items:
            lines.append(
                "No evidence collected."
            )
            continue

        for item in items:

            lines.append(
                f"- Evidence: {item.evidence}"
            )

            lines.append(
                f"  Strength: {item.strength}"
            )

            lines.append(
                f"  Confidence: {item.confidence}"
            )

    return "\n".join(lines)


# ============================================================
# ANALYZE CANDIDATE ANSWER
# ============================================================

async def analyze_candidate_answer(
    state: AssessmentState,
    answer: str,
):
    # Fairness guarantee: a candidate's refusal statement alone must never be evaluated for negative evidence
    if is_candidate_refusal(answer):
        print(f"[Agent] analyze_candidate_answer: skipping behavioral evaluation for refusal turn: {answer[:50]}")
        return

    prompt = f"""
You are a behavioral assessment evidence analyst.

Analyze the candidate's latest answer.

Behavioral dimensions:

1. communication
2. teamwork
3. adaptability
4. accountability
5. decision_making
6. leadership
7. problem_solving

Candidate answer:

{answer}

Return ONLY valid JSON.

Use this format:

{{
  "evidence": [
    {{
      "area": "communication",
      "evidence": "short factual observation",
      "strength": "weak|moderate|strong",
      "confidence": 0.0
    }}
  ]
}}

Rules:

- Only include dimensions supported by the answer.
- Do not invent evidence.
- Do not judge personality.
- Do not make hiring decisions.
- Evidence must describe observable behavior.
- confidence must be between 0 and 1.
- Keep evidence concise.
"""

    raw_text = None
    try:
        response = await openai_client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        raw_text = getattr(response, "output_text", None)
    except Exception as resp_err:
        print(f"[Agent] analyze_candidate_answer responses.create failed ({resp_err!r}). Falling back to chat.completions.create...")

    if not raw_text:
        try:
            chat_resp = await openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            raw_text = chat_resp.choices[0].message.content or ""
        except Exception as chat_err:
            print(f"[Agent] analyze_candidate_answer chat.completions fallback failed ({chat_err!r})")

    if raw_text:
        try:
            result = extract_json(raw_text)

            for item in result.get(
                "evidence",
                [],
            ):

                area = item.get(
                    "area"
                )

                evidence = item.get(
                    "evidence",
                    "",
                )

                strength = item.get(
                    "strength",
                    "unknown",
                )

                confidence = float(
                    item.get(
                        "confidence",
                        0.0,
                    )
                )

                if (
                    area in BEHAVIORAL_AREAS
                    and evidence
                ):

                    state.add_evidence(
                        area=area,
                        evidence=evidence,
                        strength=strength,
                        confidence=confidence,
                    )
        except Exception as parse_err:
            print(f"[Agent] analyze_candidate_answer parsing error: {parse_err!r}")


# ============================================================
# GENERATE ADAPTIVE QUESTION
# ============================================================

FALLBACK_BEHAVIORAL_QUESTIONS = [
    "Can you tell me about a challenging technical or operational problem you encountered and the specific steps you took to resolve it?",
    "Could you share a situation where you had to collaborate closely with a team member who had a different perspective or approach?",
    "Tell me about a time when project priorities or requirements changed unexpectedly. How did you adapt your approach?",
    "Describe a complex or high-stakes decision you had to make with limited or ambiguous information. What was your reasoning?",
    "Can you share an experience where you had to take ownership of a mistake or setback, and what steps you took afterward?",
    "Tell me about a situation where you stepped up to lead an initiative or guide colleagues through a difficult milestone?",
    "Can you describe how you communicated complex concepts or persuaded cross-functional stakeholders to align on a direction?",
]

SAFE_NEUTRAL_QUESTIONS = [
    "Could you tell me about what motivated you to pursue your current career path?",
    "What professional accomplishment or project are you most proud of in your career?",
    "How do you typically approach organizing your daily workflow and priorities?",
    "What is an area of technology or skill that you have recently enjoyed learning?",
]

async def generate_adaptive_question(
    state: AssessmentState,
    remaining_seconds: int,
    current_answer: str = "",
    transcript: str | None = None,
    evidence: str | None = None,
    is_refusal: bool = False,
    refused_question: str = "",
    **kwargs,
) -> str:

    # Circuit-breaker: if current_answer is clearly an off-topic entertainment monologue,
    # bypass LLM generation entirely so the model cannot build a question around movies/entertainment.
    if current_answer and is_off_topic_answer(current_answer):
        print(f"[Agent] generate_adaptive_question invoked with off-topic candidate answer ('{current_answer[:60]}'). Bypassing LLM generation and returning safe workplace fallback.")
        return get_next_redirect_question(state)

    # Capture the adaptive-question context before the analysis task
    # mutates state.evidence. The current answer is passed explicitly
    # so the question generator can use the latest response even while
    # evidence analysis runs in parallel.
    if transcript is None:
        transcript = format_transcript(state)

    if evidence is None:
        evidence = format_evidence(state)

    refusal_guidance = ""
    if is_refusal:
        refusal_guidance = f"""
SPECIAL INSTRUCTION - CANDIDATE REFUSAL / SKIP REQUEST:
The candidate politely declined to answer or requested to skip the previous question:
"{refused_question or 'the previous question'}"

You must handle this refusal professionally, neutrally, and respectfully:
1. Begin your response with a courteous, professional acknowledgment and neutral notification:
   "I understand. We can move to another relevant area. Please note that skipping a question may limit the evidence available for assessing this competency."
2. Immediately follow with a fresh, relevant question exploring a COMPLETELY DIFFERENT behavioral competency or situational workplace scenario.
3. Do NOT repeat the declined question or press the candidate on the declined topic.
4. Do NOT shame, threaten, argue with, or repeatedly pressure the candidate.
5. Example format:
   "I understand. We can move to another relevant area. Please note that skipping a question may limit the evidence available for assessing this competency. Could you describe how you would handle a challenging situation at work?"
"""

    previously_refused_section = ""
    if getattr(state, "refused_questions", None):
        refused_items = "\n".join(f"- {q}" for q in state.refused_questions)
        previously_refused_section = f"""
Previously declined or skipped questions:
{refused_items}
Do NOT ask about these topics or situations again, even with different wording. Pivot to an entirely different behavioral dimension.
"""

    prompt = f"""
You are an adaptive behavioral interviewer.

You are conducting a voice-based behavioral assessment.

The assessment dimensions are:

- communication
- teamwork
- adaptability
- accountability
- decision_making
- leadership
- problem_solving

Candidate stated background / introduction:
{state.candidate_introduction or "Not provided"}

The candidate's previous conversation:

{transcript}

Current behavioral evidence:

{evidence}

Candidate's latest answer:

{current_answer}

{refusal_guidance}
{previously_refused_section}
Time remaining:

{remaining_seconds} seconds.

Generate ONE natural follow-up question.

Important:

- Ground your questions strictly in the candidate's stated background, introduction, and previous answers.
- Questions must be directly relevant to the candidate's answer, role/interview context, and the target behavioral competency.
- Never invent or assume candidate qualifications, technical skills, job titles, companies, or experiences that the candidate has not explicitly stated.
- NEVER ask questions about movies, movie nights, films, cinema, actors, television shows, video games, sports, celebrities, or personal non-work entertainment.
- Even if the candidate mentions movies or entertainment, do NOT generate scenarios about organizing movie nights, watching films, or personal entertainment activities.
- All behavioral questions must focus strictly on professional workplace skills, job-related projects, engineering/business challenges, teamwork, or technical problem solving.
- Do NOT follow a fixed scenario list.
- Do NOT ask random generic or unrelated questions.
- Choose the next question based specifically on the candidate's previous response and interview context.
- Identify behavioral areas where stronger evidence is useful.
- When the candidate declines a question, pivot to a different behavioral competency while keeping the scenario relevant to their background.
- Do not shame, argue with, threaten, or pressure the candidate.
- Create a realistic workplace situation when appropriate.
- Ask what the candidate would do or what they actually did.
- Ask for a concrete example when useful.
- Avoid repeating something already answered or already refused.
- Do not ask about scores.
- Do not mention internal analysis.
- Do not mention the assessment system.
- Do not mention the timer.
- Do not make hiring decisions.
- Keep the question conversational.
- Ask only ONE question.

The question should feel like a natural continuation of the candidate's previous answer.

Return ONLY the question text.
"""

    raw_question = None
    try:
        response = await openai_client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        raw_question = getattr(response, "output_text", None)
        if raw_question:
            raw_question = raw_question.strip()
    except Exception as resp_err:
        print(f"[Agent] generate_adaptive_question responses.create failed ({resp_err!r}). Falling back to chat.completions.create...")

    if not raw_question:
        try:
            chat_resp = await openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_question = (chat_resp.choices[0].message.content or "").strip()
        except Exception as chat_err:
            print(f"[Agent] generate_adaptive_question chat.completions fallback also failed ({chat_err!r})")

    refused_list = getattr(state, "refused_questions", [])
    refused_normalized = {
        normalize_question(q)
        for q in refused_list
        if normalize_question(q)
    }

    asked_normalized = {
        normalize_question(item.get("text", ""))
        for item in state.transcript
        if item.get("role") in ("interviewer", "assistant", "agent")
    }
    for q in getattr(state, "asked_questions", []):
        if q and normalize_question(q):
            asked_normalized.add(normalize_question(q))

    if raw_question:
        # Strip code fences or surrounding quotes if returned by the model
        if raw_question.startswith("```"):
            nl = raw_question.find("\n")
            if nl != -1:
                raw_question = raw_question[nl + 1:]
            if raw_question.endswith("```"):
                raw_question = raw_question[:-3]
            raw_question = raw_question.strip()
        if (raw_question.startswith('"') and raw_question.endswith('"')) or (raw_question.startswith("'") and raw_question.endswith("'")):
            raw_question = raw_question[1:-1].strip()

        if len(raw_question) > 10:
            raw_norm = normalize_question(raw_question)
            if raw_norm in refused_normalized or any(len(r) >= 15 and (r in raw_norm or raw_norm in r) for r in refused_normalized):
                print(f"[Agent] Model-generated question matches previously refused question. Discarding and using alternative safe fallback.")
            elif refused_list and is_refused_topic_repetition(raw_question, refused_list):
                print(f"[Agent] Model-generated question repeats refused topic ('{raw_question[:60]}'). Discarding and using alternative safe fallback.")
            elif raw_norm in asked_normalized or any(len(a) >= 15 and (a in raw_norm or raw_norm in a) for a in asked_normalized):
                print(f"[Agent] Model-generated question matches previously asked question ('{raw_question[:60]}'). Discarding and selecting next unanswered question.")
            elif is_unrelated_entertainment_question(raw_question):
                print(f"[Agent] Model-generated question mentions unrelated entertainment/movies ('{raw_question[:60]}'). Discarding and using alternative safe workplace fallback.")
            else:
                if is_refusal:
                    raw_question = format_refusal_response(raw_question)
                return raw_question

    # Diverse fallback selection ensuring no question is duplicated or re-asked if refused
    excluded_normalized = asked_normalized | refused_normalized

    refused_area = get_question_behavioral_area(refused_question) if is_refusal and refused_question else ""

    # 1. Unasked predefined behavioral fallback that was not refused and does not match any refused topic
    candidate_fallbacks = list(FALLBACK_BEHAVIORAL_QUESTIONS)
    if is_refusal and refused_area:
        # Prioritize fallbacks from a DIFFERENT behavioral area than the refused question
        candidate_fallbacks.sort(key=lambda fb: 1 if get_question_behavioral_area(fb) == refused_area else 0)

    for fallback in candidate_fallbacks:
        fb_norm = normalize_question(fallback)
        if (
            fb_norm not in excluded_normalized
            and not is_refused_topic_repetition(fallback, refused_list)
            and not is_unrelated_entertainment_question(fallback)
        ):
            return format_refusal_response(fallback) if is_refusal else fallback

    # 2. Unasked safe neutral question from another behavioral dimension that was not refused
    for safe_q in SAFE_NEUTRAL_QUESTIONS:
        sq_norm = normalize_question(safe_q)
        if (
            sq_norm not in excluded_normalized
            and not is_refused_topic_repetition(safe_q, refused_list)
            and not is_unrelated_entertainment_question(safe_q)
        ):
            return format_refusal_response(safe_q) if is_refusal else safe_q

    # 3. Predefined fallback asked previously, but NEVER refused and not matching any refused topic
    for fallback in FALLBACK_BEHAVIORAL_QUESTIONS:
        fb_norm = normalize_question(fallback)
        if (
            fb_norm not in refused_normalized
            and not is_refused_topic_repetition(fallback, refused_list)
        ):
            return format_refusal_response(fallback) if is_refusal else fallback

    # 4. Safe neutral question that was NEVER refused and does not match any refused topic
    for safe_q in SAFE_NEUTRAL_QUESTIONS:
        sq_norm = normalize_question(safe_q)
        if (
            sq_norm not in refused_normalized
            and not is_refused_topic_repetition(safe_q, refused_list)
        ):
            return format_refusal_response(safe_q) if is_refusal else safe_q

    # Controlled no-question result: every predefined and safe fallback was refused or matches refused topics
    print("[Agent] All fallback and safe neutral questions are excluded or match refused topics. Concluding question generation safely.")
    return ""


# ============================================================
# GENERATE FINAL ANALYSIS
# ============================================================

async def generate_final_analysis(
    state: AssessmentState,
):

    transcript = format_full_transcript(
        state
    )

    evidence = format_full_evidence(
        state
    )

    prompt = f"""
You are generating a professional behavioral assessment.

Use ONLY the evidence supported by the transcript.

Transcript:

{transcript}

Behavioral evidence:

{evidence}

Fairness & Assessment Rules:
- Base evaluations solely on substantive answers and observable evidence provided by the candidate.
- If the candidate politely declined, skipped, or requested to redirect a specific question, do NOT treat the refusal itself as evidence of poor communication, lack of teamwork, low adaptability, or weak leadership.
- Do NOT invent or assume candidate qualifications, skills, or experiences not stated in the transcript.

Evaluate these dimensions:

- communication
- teamwork
- adaptability
- accountability
- decision_making
- leadership
- problem_solving

Return ONLY valid JSON.

Required structure:

{{
  "scores": {{
    "communication": 0,
    "teamwork": 0,
    "adaptability": 0,
    "accountability": 0,
    "decision_making": 0,
    "leadership": 0,
    "problem_solving": 0
  }},

  "overall_score": 0,

  "overall_communication": "",

  "strengths": [
    "",
    "",
    ""
  ],

  "areas_for_improvement": [
    "",
    ""
  ],

  "behavioral_summary": "",

  "team_working_style": {{
    "collaboration": 0,
    "independence": 0,
    "adaptability": 0,
    "leadership": 0,
    "structure": 0,
    "autonomy": 0,
    "summary": "",
    "environment_alignment": [
      "",
      "",
      ""
    ]
  }}
}}

Rules:

- Scores must be integers from 0 to 100.
- Base scores on evidence.
- Do not invent evidence.
- If evidence is limited, use moderate scores and explain limitations.
- overall_score should reflect the seven behavioral scores.
- Keep written sections concise and professional.
- Do not make a hiring decision.
- Do not say hire, reject, select, or recommend hiring.
- Describe observed behavioral patterns only.
"""

    raw_text = None
    last_error = None

    try:
        response = await openai_client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        raw_text = getattr(response, "output_text", None)
    except Exception as resp_err:
        last_error = resp_err
        print(f"[Agent] openai_client.responses.create failed ({resp_err!r}). Falling back to chat.completions.create...")

    if not raw_text:
        try:
            chat_resp = await openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            raw_text = chat_resp.choices[0].message.content or ""
        except Exception as chat_err:
            print(f"[Agent] chat.completions fallback also failed ({chat_err!r})")
            last_error = chat_err
            raise RuntimeError(f"OpenAI final analysis generation failed: {last_error}") from last_error

    try:
        result = extract_json(raw_text)

        if not result or not isinstance(result, dict):
            raise RuntimeError(
                "OpenAI returned an empty or invalid final analysis JSON structure."
            )

        # Ensure required structure keys exist with valid fallbacks
        if "scores" not in result or not isinstance(result["scores"], dict):
            result["scores"] = {area: 50 for area in BEHAVIORAL_AREAS}
        for area in BEHAVIORAL_AREAS:
            if area not in result["scores"]:
                result["scores"][area] = 50

        if "overall_score" not in result or not isinstance(result["overall_score"], (int, float)):
            result["overall_score"] = int(sum(result["scores"].values()) / max(len(result["scores"]), 1))

        if "overall_communication" not in result or not result["overall_communication"]:
            result["overall_communication"] = "Communicated clearly and responded to interview scenarios."

        if "strengths" not in result or not isinstance(result["strengths"], list):
            result["strengths"] = ["Demonstrated engaged participation throughout the interview."]

        if "areas_for_improvement" not in result or not isinstance(result["areas_for_improvement"], list):
            result["areas_for_improvement"] = ["Provide additional concrete examples in future behavioral scenarios."]

        if "behavioral_summary" not in result or not result["behavioral_summary"]:
            result["behavioral_summary"] = "Candidate completed the behavioral assessment session."

        if "team_working_style" not in result or not isinstance(result["team_working_style"], dict):
            result["team_working_style"] = {
                "collaboration": 50,
                "independence": 50,
                "adaptability": 50,
                "leadership": 50,
                "structure": 50,
                "autonomy": 50,
                "summary": "Demonstrated balanced behavioral working style.",
                "environment_alignment": ["Collaborative teams", "Structured environments"],
            }

        state.final_analysis = result
        return result

    except Exception as error:

        print(
            "Final analysis parsing error:",
            repr(error),
        )

        raise RuntimeError(
            f"Final behavioral analysis could not be generated: {error}"
        ) from error


# ============================================================
# SEND DATA TO FRONTEND
# ============================================================

async def send_ui_event(
    ctx: JobContext,
    event_type: str,
    data: dict | None = None,
):

    payload = {
        "type": event_type,
        "data": data or {},
    }

    try:

        encoded = json.dumps(
            payload
        ).encode("utf-8")

        await ctx.room.local_participant.publish_data(
            encoded,
            reliable=True,
            topic="behavioral-ui",
        )

    except Exception as error:

        print(
            "UI event error:",
            repr(error),
        )


# ============================================================
# BEHAVIORAL INTERVIEW AGENT
# ============================================================

class BehavioralInterviewAgent(
    Agent
):

    def __init__(self, on_turn_completed_callback=None):

        super().__init__(
            instructions="""
You are a professional Behavioral AI interviewer.

Your job is to conduct a structured behavioral assessment.

Important:

The interview is adaptive.

You must not follow a predefined scenario list.

Questions are generated dynamically from the candidate's previous responses.

The assessment evaluates:

Communication
Teamwork
Adaptability
Accountability
Decision Making
Leadership
Problem Solving

The candidate should answer naturally by voice.

Do not discuss scores during the interview.

Do not make hiring decisions.

Keep spoken responses short and natural.

The interview controller will decide when to ask the next question.
"""
        )
        self._on_turn_completed_callback = on_turn_completed_callback

    async def on_user_turn_completed(
        self,
        chat_ctx,
        new_message,
    ):
        if self._on_turn_completed_callback and new_message:
            try:
                text = (
                    getattr(new_message, "text_content", None)
                    or getattr(new_message, "content", None)
                    or ""
                )
                if isinstance(text, list):
                    text = " ".join(str(t) for t in text)
                text = str(text).strip()
                if text:
                    print(f"[Agent Turn] on_user_turn_completed triggered ({len(text.split())} words, {len(text)} chars): '{text}'")
                    res = self._on_turn_completed_callback(text)
                    if asyncio.iscoroutine(res):
                        await res
            except Exception as cb_err:
                print(f"[Agent] on_turn_completed_callback warning: {cb_err!r}")

        # We manually control every interviewer response.
        raise StopResponse()


# ============================================================
# LIFECYCLE & TURN MANAGEMENT HELPERS
# ============================================================

def compute_sync_signature(assessment_id: str, secret: str) -> str:
    """Compute HMAC-SHA256 signature for authenticating assessment result sync requests."""
    return hmac.new(
        secret.strip().encode("utf-8"),
        assessment_id.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def sync_assessment_to_backend(
    state: AssessmentState,
    assessment_id: str,
    backend_url: str | None = None,
    timeout: float = 3.5,
) -> bool:
    """Synchronize completed assessment result to backend HTTP endpoint with bounded timeout."""
    resolved_url = (
        backend_url
        or os.getenv("BACKEND_URL")
        or os.getenv("REACT_APP_API_BASE_URL")
        or "https://behavioral-backend-gc62.onrender.com"
    )
    if not resolved_url:
        return False
    try:
        import urllib.request
        sync_payload = json.dumps(state.to_dict()).encode("utf-8")
        sync_url = f"{resolved_url.rstrip('/')}/assessment-result?assessmentId={assessment_id}"
        headers = {"Content-Type": "application/json"}
        sync_secret = (
            os.getenv("ASSESSMENT_SYNC_SECRET")
            or os.getenv("LIVEKIT_API_SECRET")
            or ""
        ).strip()
        if sync_secret:
            headers["X-Assessment-Signature"] = compute_sync_signature(
                assessment_id, sync_secret
            )
        req = urllib.request.Request(
            sync_url,
            data=sync_payload,
            headers=headers,
            method="POST",
        )
        def _post_sync():
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status == 200
        ok = await asyncio.to_thread(_post_sync)
        print("[Agent] Assessment result synced to backend HTTP endpoint.")
        return bool(ok)
    except Exception as sync_err:
        print("[Agent] Backend sync notice (non-fatal):", repr(sync_err))
        return False


async def deliver_final_result_and_sync(
    ctx,
    state: AssessmentState,
    assessment_id: str,
    candidate_disconnected: asyncio.Event,
    send_event_fn=send_ui_event,
    save_fn=save_assessment,
    sync_fn=sync_assessment_to_backend,
    disconnect_timeout: float = 4.0,
) -> None:
    """Send final_result over LiveKit immediately without blocking on disk/HTTP, then persist & sync concurrently."""
    if getattr(state, "is_finalized", False):
        print("[Agent] Assessment already finalized; skipping duplicate delivery/sync.")
        return
    state.is_finalized = True
    state.completed = True

    try:
        await send_event_fn(
            ctx,
            "final_result",
            state.to_dict(),
        )
        print("FINAL RESULT SENT TO FRONTEND.")
    except Exception as error:
        print("Final result send failed:", repr(error))

    async def _wait_disconnect():
        try:
            await asyncio.wait_for(
                candidate_disconnected.wait(),
                timeout=disconnect_timeout,
            )
        except asyncio.TimeoutError:
            pass

    await asyncio.gather(
        asyncio.to_thread(save_fn, state, assessment_id),
        sync_fn(state, assessment_id),
        _wait_disconnect(),
        return_exceptions=True,
    )


def handle_user_input_transcribed_event(
    transcript: str,
    is_final: bool,
    is_agent_speaking: bool,
    uncommitted_final_segments: list[str],
    send_ui_event_fn=None,
    active_question: str = "",
) -> bool:
    """Handle a user transcript event from STT.

    Buffers finalized segments and dispatches full accumulated interim transcript
    so the UI candidate bubble is never truncated or replaced with a single word.
    Safely ignores acoustic echoes of the active interviewer question during speech playback.
    """
    if not transcript:
        return False

    transcript = transcript.strip()
    if not transcript:
        return False

    # Check for acoustic echo: ignore only if agent is actively speaking AND transcript echoes the question
    if is_agent_speaking and active_question:
        clean_t = re.sub(r"[^\w\s]", "", transcript.lower()).strip()
        clean_q = re.sub(r"[^\w\s]", "", active_question.lower()).strip()
        if clean_t and clean_t in clean_q and len(clean_t.split()) >= 2:
            print(f"[Agent Audio] Ignoring confirmed acoustic echo during interviewer speech: '{clean_t}'")
            return False

    print(
        f"[Agent STT] Transcript event received: final={is_final}, chars={len(transcript)}, words={len(transcript.split())}"
    )

    if is_final:
        uncommitted_final_segments.append(transcript)
        accumulated = " ".join(s for s in uncommitted_final_segments if s).strip()
        if send_ui_event_fn is not None and accumulated:
            send_ui_event_fn(
                "candidate_interim_transcript",
                {
                    "answer": accumulated,
                    "is_final": False,
                },
            )
    else:
        parts = [s for s in uncommitted_final_segments if s] + [transcript]
        accumulated = " ".join(parts).strip()
        if send_ui_event_fn is not None and accumulated:
            send_ui_event_fn(
                "candidate_interim_transcript",
                {
                    "answer": accumulated,
                    "is_final": False,
                },
            )
    return True


async def finalize_uncommitted_speech(
    session,
    uncommitted_final_segments: list[str],
    on_turn_callback,
    commit_timeout: float = 1.5,
) -> str | None:
    """Commit any in-flight user turn via supported AgentSession API and flush finalized STT segments."""
    snapshot_segments = [s.strip() for s in uncommitted_final_segments if s and s.strip()]
    uncommitted_final_segments.clear()
    committed_text = ""

    if session is not None and hasattr(session, "commit_user_turn"):
        try:
            commit_fut = session.commit_user_turn(
                transcript_timeout=1.0,
                stt_flush_duration=0.8,
                skip_reply=True,
            )
            if asyncio.isfuture(commit_fut) or asyncio.iscoroutine(commit_fut):
                res = await asyncio.wait_for(commit_fut, timeout=commit_timeout)
                if isinstance(res, str) and res.strip():
                    committed_text = res.strip()
        except Exception as commit_err:
            print(f"[Agent] commit_user_turn notice during finalization: {commit_err!r}")

    final_text = committed_text or (" ".join(snapshot_segments).strip() if snapshot_segments else "")
    if final_text and on_turn_callback is not None:
        res = on_turn_callback(final_text)
        if asyncio.iscoroutine(res):
            await res
        return final_text
    return None


async def drain_and_analyze_pending_answers(
    state: AssessmentState,
    user_answer_queue: asyncio.Queue,
    analyzed_answers: set[str],
    answer_task: asyncio.Task | None = None,
    extra_answers: list[str] | None = None,
    analyze_fn=analyze_candidate_answer,
) -> list[str]:
    """Rescue any completed or pending answer_task result and drain user_answer_queue, analyzing each valid answer once."""
    collected: list[str] = []
    if extra_answers:
        for item in extra_answers:
            if isinstance(item, str) and item.strip():
                collected.append(item.strip())

    if answer_task is not None:
        if not answer_task.done():
            answer_task.cancel()
            try:
                rescued = await answer_task
                if isinstance(rescued, str) and rescued.strip():
                    collected.append(rescued.strip())
            except (asyncio.CancelledError, Exception):
                pass
        else:
            if not answer_task.cancelled() and answer_task.exception() is None:
                try:
                    rescued = answer_task.result()
                    if isinstance(rescued, str) and rescued.strip():
                        collected.append(rescued.strip())
                except Exception:
                    pass

    while not user_answer_queue.empty():
        try:
            queued_ans = user_answer_queue.get_nowait()
            if isinstance(queued_ans, str) and queued_ans.strip():
                collected.append(queued_ans.strip())
        except Exception:
            break

    newly_analyzed: list[str] = []
    for ans in collected:
        ans_clean = ans.strip()
        if not ans_clean:
            continue
        ans_key = ans_clean.lower()
        if ans_key in analyzed_answers:
            continue
        analyzed_answers.add(ans_key)
        if (
            state.questions_asked == 0
            and not state.candidate_introduction
            and not is_non_introduction_turn(ans_clean, state)
        ):
            state.candidate_introduction = ans_clean
        try:
            print(f"[Agent] Analyzing pending candidate response: {ans_clean[:60]}...")
            await analyze_fn(state, ans_clean)
            newly_analyzed.append(ans_clean)
        except Exception as drain_err:
            print("[Agent] Pending answer analysis notice:", repr(drain_err))

    return newly_analyzed


async def speak_with_lock(
    session,
    text: str,
    speech_lock: asyncio.Lock,
    stage_label: str = "Speech",
    manual_end_event: asyncio.Event | None = None,
    disconnect_event: asyncio.Event | None = None,
    set_speaking_state=None,
    pause_event: asyncio.Event | None = None,
    resume_event: asyncio.Event | None = None,
    is_paused_fn=None,
) -> bool:
    """Serialize session.say calls with speech_lock and handle End Assessment / disconnect / pause cleanly during playback."""
    if (manual_end_event and manual_end_event.is_set()) or (
        disconnect_event and disconnect_event.is_set()
    ):
        return False

    if is_paused_fn and is_paused_fn():
        if resume_event is not None:
            await resume_event.wait()
            if (manual_end_event and manual_end_event.is_set()) or (
                disconnect_event and disconnect_event.is_set()
            ):
                return False
        else:
            return False

    async with speech_lock:
        if (manual_end_event and manual_end_event.is_set()) or (
            disconnect_event and disconnect_event.is_set()
        ):
            return False

        if is_paused_fn and is_paused_fn():
            if resume_event is not None:
                await resume_event.wait()
                if (manual_end_event and manual_end_event.is_set()) or (
                    disconnect_event and disconnect_event.is_set()
                ):
                    return False
            else:
                return False

        if set_speaking_state is not None:
            set_speaking_state(True)

        playout_task: asyncio.Task | None = None
        event_tasks: set[asyncio.Task] = set()
        try:
            print(f"[Agent] [{stage_label}] Queuing speech delivery via TTS: {text[:60]}...")
            handle = session.say(text, allow_interruptions=False)
            start_t = time.monotonic()

            async def _await_handle():
                return await handle

            playout_task = asyncio.create_task(_await_handle())
            wait_set: set[asyncio.Task] = {playout_task}

            if manual_end_event is not None:
                t_manual = asyncio.create_task(manual_end_event.wait())
                event_tasks.add(t_manual)
                wait_set.add(t_manual)

            if disconnect_event is not None:
                t_disc = asyncio.create_task(disconnect_event.wait())
                event_tasks.add(t_disc)
                wait_set.add(t_disc)

            t_pause = None
            if pause_event is not None:
                t_pause = asyncio.create_task(pause_event.wait())
                event_tasks.add(t_pause)
                wait_set.add(t_pause)

            done, _ = await asyncio.wait(
                wait_set,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if playout_task not in done:
                reason = "pause request" if (t_pause and t_pause in done) else "assessment end/disconnect"
                print(f"[Agent] [{stage_label}] Stopping active speech due to {reason}.")
                try:
                    if hasattr(handle, "interrupt"):
                        handle.interrupt(force=True)
                    elif hasattr(session, "interrupt"):
                        session.interrupt(force=True)
                except Exception:
                    pass
                playout_task.cancel()
                try:
                    await playout_task
                except (asyncio.CancelledError, Exception):
                    pass
                return False

            dur = time.monotonic() - start_t
            err = handle.exception() if hasattr(handle, "exception") else None
            if err:
                print(f"[Agent] [{stage_label}] Speech FAILED after {dur:.2f}s: {err!r}")
            else:
                print(f"[Agent] [{stage_label}] Speech playout completed without errors in {dur:.2f}s")
            return True
        finally:
            for t in event_tasks:
                if not t.done():
                    t.cancel()
            if event_tasks:
                await asyncio.gather(*event_tasks, return_exceptions=True)
            if set_speaking_state is not None:
                set_speaking_state(False)


async def process_interview_turn(
    ctx,
    state: AssessmentState,
    answer: str,
    remaining_seconds: int,
    warning_sent: bool,
    user_answer_queue: asyncio.Queue,
    analyzed_answers: set[str],
    manual_end_event: asyncio.Event,
    timer_finished_event: asyncio.Event,
    disconnect_event: asyncio.Event,
    speak_fn,
    analyze_fn=analyze_candidate_answer,
    generate_question_fn=generate_adaptive_question,
    send_event_fn=send_ui_event,
    is_paused_fn=None,
    resume_event: asyncio.Event | None = None,
) -> bool:
    """Analyze candidate answer, drain any continuation turns arriving during LLM generation, and ask next question if active."""
    answer = (answer or "").strip()
    if not answer:
        return True

    # If already ending, finished, or disconnected, reject any new question generation
    if (
        state.completed
        or getattr(state, "is_finalized", False)
        or manual_end_event.is_set()
        or timer_finished_event.is_set()
        or disconnect_event.is_set()
    ):
        return False

    # If currently paused, wait until resumed before advancing turn logic
    if is_paused_fn and is_paused_fn():
        if resume_event is not None:
            print("[Turn Routing] Turn processing waiting on resume_event...")
            await resume_event.wait()
            if (
                state.completed
                or getattr(state, "is_finalized", False)
                or manual_end_event.is_set()
                or timer_finished_event.is_set()
                or disconnect_event.is_set()
            ):
                return False
        else:
            return False

    # Precedence:
    # 1. Candidate Ending Intent (honored even if abusive words present!)
    # 2. Ambiguous Ending Request (asks clarification)
    # 3. Candidate Frustration (calm de-escalation, avoids immediately repeating question)
    # 4. Abusive language (issues professional warning, never mirrors abuse)
    # 5. Topic choice response (when interviewer is waiting for candidate to choose between rephrasing and moving on)
    # 6. Topic-change / neutral topic preference (polite acknowledgment, brief neutral engagement)
    # 7. Boundary request
    # 8. Question refusal
    # 9. Clarification request
    # 10. Off-topic answer
    # 11. Substantive answer
    is_end = is_candidate_end_request(answer)
    is_ambiguous = not is_end and is_ambiguous_end_request(answer)
    is_frustration = not is_end and not is_ambiguous and is_candidate_frustration(answer)
    is_abuse = not is_end and not is_ambiguous and not is_frustration and is_abusive_language(answer)

    is_waiting_choice = bool(getattr(state, "waiting_for_topic_choice", False))
    is_choice_move = (
        not is_end
        and not is_ambiguous
        and not is_frustration
        and not is_abuse
        and is_waiting_choice
        and is_choice_to_move_to_another_question(answer)
    )
    is_choice_rephrase = (
        not is_end
        and not is_ambiguous
        and not is_frustration
        and not is_abuse
        and not is_choice_move
        and is_waiting_choice
        and (
            any(w in answer.lower() for w in ("rephrase", "repeat the question", "clarify the question"))
            or is_clarification_request(answer)
        )
    )
    is_choice_resume = (
        not is_end
        and not is_ambiguous
        and not is_frustration
        and not is_abuse
        and not is_choice_move
        and not is_choice_rephrase
        and is_waiting_choice
        and is_permission_to_resume(answer)
    )
    is_choice_ack = (
        not is_end
        and not is_ambiguous
        and not is_frustration
        and not is_abuse
        and not is_choice_move
        and not is_choice_rephrase
        and not is_choice_resume
        and is_waiting_choice
        and is_bare_acknowledgment(answer)
    )
    is_choice_moment = (
        not is_end
        and not is_ambiguous
        and not is_frustration
        and not is_abuse
        and not is_choice_move
        and not is_choice_rephrase
        and not is_choice_resume
        and not is_choice_ack
        and is_waiting_choice
        and any(w in answer.lower() for w in ("another moment", "a minute", "a moment", "not yet", "give me", "need time", "just a sec"))
    )
    is_topic_choice = is_choice_move or is_choice_rephrase or is_choice_resume or is_choice_ack or is_choice_moment

    is_waiting_resume = (not is_waiting_choice) and (
        bool(getattr(state, "waiting_for_resume", False))
        or state.neutral_topic_turns > 0
        or bool(state.preferred_neutral_topic)
    )
    is_topic_pref = not is_end and not is_ambiguous and not is_frustration and not is_abuse and not is_topic_choice and (
        is_explicit_topic_change_preference(answer)
        or (is_waiting_resume and (
            is_bare_acknowledgment(answer)
            or is_permission_to_resume(answer)
            or any(w in answer.lower() for w in ("another moment", "a minute", "a moment", "not yet", "give me", "need time", "just a sec"))
        ))
    )
    is_boundary = not is_end and not is_ambiguous and not is_frustration and not is_abuse and not is_topic_choice and not is_topic_pref and is_personal_or_unrelated_request(answer)
    is_refusal = not is_end and not is_ambiguous and not is_frustration and not is_abuse and not is_topic_choice and not is_topic_pref and not is_boundary and is_candidate_refusal(answer)
    is_clarification = not is_end and not is_ambiguous and not is_frustration and not is_abuse and not is_topic_choice and not is_topic_pref and not is_boundary and not is_refusal and is_clarification_request(answer)
    is_off_topic = not is_end and not is_ambiguous and not is_frustration and not is_abuse and not is_topic_choice and not is_topic_pref and not is_boundary and not is_refusal and not is_clarification and is_off_topic_answer(answer)
    is_non_substantive = is_end or is_ambiguous or is_frustration or is_abuse or is_topic_choice or is_topic_pref or is_boundary or is_refusal or is_clarification or is_off_topic

    if not any(item.get("role") == "candidate" and item.get("text") == answer for item in state.transcript):
        state.add_transcript("candidate", answer)

    if state.questions_asked == 0 and not state.candidate_introduction and not is_non_substantive and not is_non_introduction_turn(answer, state):
        state.candidate_introduction = answer

    refused_question = ""
    if is_end:
        print(f"[Agent] Candidate interview ending intent detected: '{answer[:80]}'")
    elif is_ambiguous:
        print(f"[Agent] Ambiguous candidate ending intent detected: '{answer[:80]}'. Asking clarification.")
    elif is_frustration:
        state.frustration_count += 1
        print(f"[Agent] Candidate frustration detected (count={state.frustration_count}): '{answer[:80]}'. De-escalating calmly.")
    elif is_abuse:
        state.abusive_language_count += 1
        print(f"[Agent] Abusive language detected (count={state.abusive_language_count}). Issuing professional warning.")
    elif is_topic_choice:
        print(f"[Agent] Candidate topic choice response detected (move={is_choice_move}, rephrase={is_choice_rephrase}, ack={is_choice_ack}): '{answer[:80]}'")
    elif is_topic_pref:
        print(f"[Agent] Candidate neutral topic preference detected: '{answer[:80]}'. Engaging naturally.")
    elif is_boundary:
        print("[Agent] Candidate personal/unrelated topic request detected. Setting professional boundary.")
    elif is_refusal:
        refused_question = get_last_interviewer_question(state)
        if refused_question and refused_question not in state.refused_questions:
            state.refused_questions.append(refused_question)
        print(f"[Agent] Candidate refusal detected. Refused question: '{refused_question[:60]}'. Bypassing behavioral penalty.")
    elif is_off_topic:
        is_repeated_off_topic_turn = is_repeated_off_topic(state, answer)
        state.off_topic_count += 1
        print(f"[Agent] Off-topic answer detected (count={state.off_topic_count}, repeated={is_repeated_off_topic_turn}). Redirecting to interview topic.")

    branch_name = (
        "end" if is_end
        else "ambiguous_end" if is_ambiguous
        else "frustration" if is_frustration
        else "abuse" if is_abuse
        else "topic_choice" if is_topic_choice
        else "topic_pref" if is_topic_pref
        else "boundary" if is_boundary
        else "refusal" if is_refusal
        else "clarification" if is_clarification
        else "off_topic" if is_off_topic
        else "substantive"
    )
    llm_generator_called = not (is_end or is_ambiguous or is_frustration or is_abuse or is_topic_choice or is_topic_pref or is_clarification or is_off_topic)
    print(f"[Turn Routing] Candidate answer: '{answer[:80]}'")
    print(
        f"[Turn Routing] Selected branch: {branch_name} | is_off_topic={is_off_topic} "
        f"(count={state.off_topic_count}) | LLM question generator called={llm_generator_called}"
    )

    # 1. CANDIDATE EXPLICIT ENDING INTENT
    if is_end:
        # Check if candidate used abusive language in this turn
        has_abusive_words = is_abusive_language(answer)
        if has_abusive_words:
            state.abusive_language_count += 1
            print("[Agent] Candidate requested to end using rude/abusive wording. Honoring end request calmly and professionally.")
            closing_resp = "I understand. We will conclude the interview here. Thank you for your time."
        else:
            closing_resp = CANDIDATE_END_CLOSING_MESSAGE

        if not any(item.get("role") == "candidate" and item.get("text") == answer for item in state.transcript):
            state.add_transcript("candidate", answer)

        # Notify UI of candidate-initiated intentional conclusion
        await send_event_fn(
            ctx,
            "assessment_closing",
            {
                "reason": "candidate_requested_end",
            },
        )

        # Speak concise closing response without insulting back
        await speak_fn(closing_resp, "Candidate-requested interview conclusion")
        state.add_transcript("interviewer", closing_resp)

        # Set manual_end_event to stop the interview loop and proceed directly to final assessment
        manual_end_event.set()
        return False

    # 2. AMBIGUOUS ENDING REQUEST (ask single polite clarification)
    if is_ambiguous and not disconnect_event.is_set() and not manual_end_event.is_set():
        if not any(item.get("role") == "candidate" and item.get("text") == answer for item in state.transcript):
            state.add_transcript("candidate", answer)
        await send_event_fn(
            ctx,
            "interview_question",
            {
                "question": AMBIGUOUS_END_CLARIFICATION_MESSAGE,
                "question_number": state.questions_asked,
            },
        )
        spoke_ok = await speak_fn(AMBIGUOUS_END_CLARIFICATION_MESSAGE, "Ambiguous end clarification")
        if spoke_ok:
            state.add_transcript("interviewer", AMBIGUOUS_END_CLARIFICATION_MESSAGE)
        return True

    if (
        warning_sent
        or remaining_seconds <= WARNING_SECONDS
        or manual_end_event.is_set()
        or timer_finished_event.is_set()
    ):
        if warning_sent:
            print("Candidate finished current response after warning.")
        if not disconnect_event.is_set() and not manual_end_event.is_set():
            if is_abuse:
                await speak_fn(
                    ABUSE_WARNING_FIRST_PREFIX if state.abusive_language_count <= 1 else ABUSE_WARNING_REPEATED_PREFIX,
                    "Abusive language warning",
                )
            elif is_frustration:
                await speak_fn(
                    "I apologize for any frustration. We will conclude our session here.",
                    "Frustration closing",
                )
            elif is_topic_choice or is_topic_pref:
                await speak_fn(
                    "I understand. We will conclude our session here.",
                    "Topic preference closing",
                )
            elif is_boundary:
                await speak_fn(
                    PROFESSIONAL_BOUNDARY_PREFIX,
                    "Professional boundary reminder",
                )
            elif is_refusal:
                await speak_fn(
                    "I understand, that is completely fine.",
                    "Refusal acknowledgment",
                )
            elif is_off_topic:
                prefix = OFF_TOPIC_FIRST_PREFIX if state.off_topic_count <= 1 else OFF_TOPIC_REPEATED_PREFIX
                await speak_fn(
                    prefix,
                    "Off-topic redirection",
                )
            else:
                await drain_and_analyze_pending_answers(
                    state,
                    user_answer_queue,
                    analyzed_answers,
                    extra_answers=[answer],
                    analyze_fn=analyze_fn,
                )
        return False

    # 3. CANDIDATE FRUSTRATION (calm de-escalation, acknowledge, apologize if repetitive, offer pause/subject change)
    if is_frustration and not disconnect_event.is_set() and not manual_end_event.is_set():
        if not any(item.get("role") == "candidate" and item.get("text") == answer for item in state.transcript):
            state.add_transcript("candidate", answer)
        frustration_resp = (
            "I apologize if I've been repetitive or if this is frustrating. "
            "We can certainly take a pause, switch to a different subject, or conclude if you prefer. "
            "How would you like to proceed?"
        )
        await send_event_fn(
            ctx,
            "interview_question",
            {
                "question": frustration_resp,
                "question_number": state.questions_asked,
            },
        )
        spoke_ok = await speak_fn(frustration_resp, "Frustration de-escalation")
        if spoke_ok:
            state.add_transcript("interviewer", frustration_resp)
        return True

    # 4. ABUSIVE LANGUAGE (calm, professional boundary, never insults back, redirects to current question)
    if is_abuse and not disconnect_event.is_set() and not manual_end_event.is_set():
        if not any(item.get("role") == "candidate" and item.get("text") == answer for item in state.transcript):
            state.add_transcript("candidate", answer)

        # 4a. Repeated abuse escalation (count >= 3: conclude interview respectfully)
        if state.abusive_language_count >= 3:
            closing_resp = ABUSE_TERMINATION_MESSAGE
            await send_event_fn(
                ctx,
                "assessment_closing",
                {
                    "reason": "repeated_abuse",
                },
            )
            spoke_ok = await speak_fn(closing_resp, "Repeated abuse interview conclusion")
            if spoke_ok:
                state.add_transcript("interviewer", closing_resp)
            manual_end_event.set()
            return False

        # 4b. Repeated abuse warning (count == 2: offer to continue respectfully or end)
        elif state.abusive_language_count == 2:
            abuse_resp = ABUSE_WARNING_REPEATED_PREFIX

        # 4c. First abuse warning (count == 1: professional boundary + redirect to current question)
        else:
            last_q = get_last_interviewer_question(state)
            current_q = strip_refusal_prefix(last_q)
            if not current_q and generate_question_fn is not None:
                try:
                    raw_gen = await generate_question_fn(state, remaining_seconds, current_answer=answer)
                    if raw_gen:
                        current_q = strip_refusal_prefix(raw_gen)
                except Exception:
                    pass
            if not current_q:
                current_q = get_next_redirect_question(state)
            abuse_resp = f"{ABUSE_WARNING_FIRST_PREFIX} {current_q}"

        await send_event_fn(
            ctx,
            "interview_question",
            {
                "question": abuse_resp,
                "question_number": state.questions_asked,
            },
        )
        spoke_ok = await speak_fn(abuse_resp, f"Abuse warning #{state.abusive_language_count}")
        if spoke_ok:
            state.add_transcript("interviewer", abuse_resp)
        return True

    # 5. TOPIC CHOICE HANDLING (when waiting for candidate to choose between rephrasing and moving on)
    if is_topic_choice and not disconnect_event.is_set() and not manual_end_event.is_set():
        if not any(item.get("role") == "candidate" and item.get("text") == answer for item in state.transcript):
            state.add_transcript("candidate", answer)

        # 5a. Check if candidate chooses to move to another relevant professional interview question
        if is_choice_move:
            state.waiting_for_topic_choice = False
            state.waiting_for_resume = False
            state.preferred_neutral_topic = ""
            state.neutral_topic_turns = 0
            state.repeat_count_for_current_question = 0
            last_q = get_last_interviewer_question(state)
            if last_q and last_q not in state.refused_questions:
                state.refused_questions.append(last_q)
            next_q = get_next_redirect_question(state)
            if next_q not in state.asked_questions:
                state.asked_questions.append(next_q)
            area = get_question_behavioral_area(next_q)
            if area and area not in state.asked_areas:
                state.asked_areas.append(area)
            state.questions_asked += 1
            move_resp = f"Certainly. Let's move to another relevant interview question: {next_q}"
            await send_event_fn(
                ctx,
                "interview_question",
                {
                    "question": move_resp,
                    "question_number": state.questions_asked,
                },
            )
            spoke_ok = await speak_fn(move_resp, f"Moved to question #{state.questions_asked}")
            if spoke_ok:
                state.add_transcript("interviewer", move_resp)
            return True

        # 5b. Check if candidate asks to rephrase the question
        elif is_choice_rephrase:
            state.waiting_for_topic_choice = False
            state.waiting_for_resume = False
            state.preferred_neutral_topic = ""
            state.neutral_topic_turns = 0
            last_q = get_last_interviewer_question(state)
            clean_q = strip_refusal_prefix(last_q) or "Could you introduce yourself and tell me about your background?"
            rephrase_resp = f"Certainly. To rephrase: {clean_q}"
            await send_event_fn(
                ctx,
                "interview_question",
                {
                    "question": rephrase_resp,
                    "question_number": state.questions_asked,
                },
            )
            spoke_ok = await speak_fn(rephrase_resp, "Rephrased question")
            if spoke_ok:
                state.add_transcript("interviewer", rephrase_resp)
            return True

        # 5c. Check if candidate agrees to resume assessment
        elif is_choice_resume:
            state.waiting_for_topic_choice = False
            state.waiting_for_resume = False
            state.preferred_neutral_topic = ""
            state.neutral_topic_turns = 0
            state.repeat_count_for_current_question = 0
            next_q = get_next_redirect_question(state)
            if next_q not in state.asked_questions:
                state.asked_questions.append(next_q)
            area = get_question_behavioral_area(next_q)
            if area and area not in state.asked_areas:
                state.asked_areas.append(area)
            state.questions_asked += 1
            resume_resp = f"Thank you. Let's return to the assessment. {next_q}"
            await send_event_fn(
                ctx,
                "interview_question",
                {
                    "question": resume_resp,
                    "question_number": state.questions_asked,
                },
            )
            spoke_ok = await speak_fn(resume_resp, f"Resumed question #{state.questions_asked}")
            if spoke_ok:
                state.add_transcript("interviewer", resume_resp)
            return True

        # 5d. Check if candidate requests a brief moment
        elif is_choice_moment:
            state.waiting_for_resume = True
            topic_resp = "Take all the time you need. Whenever you feel comfortable resuming, just let me know."

        # 5e. Bare acknowledgment like "Okay" (Rule 6: do not treat bare "Okay" as permission to move or resume)
        else:
            state.waiting_for_topic_choice = True
            state.waiting_for_resume = True
            topic_resp = "Take your time. Please let me know whether you would like me to rephrase the question or move to another relevant interview question."

        await send_event_fn(
            ctx,
            "interview_question",
            {
                "question": topic_resp,
                "question_number": state.questions_asked,
            },
        )
        spoke_ok = await speak_fn(topic_resp, "Topic choice response")
        if spoke_ok:
            state.add_transcript("interviewer", topic_resp)
        return True

    # 6. TOPIC-CHANGE / RESUME HANDLING
    if is_topic_pref and not disconnect_event.is_set() and not manual_end_event.is_set():
        if not any(item.get("role") == "candidate" and item.get("text") == answer for item in state.transcript):
            state.add_transcript("candidate", answer)

        # 6a. Check if candidate clearly agrees to resume assessment (Rule 5)
        if is_permission_to_resume(answer) and (
            bool(getattr(state, "waiting_for_resume", False))
            or state.neutral_topic_turns > 0
            or bool(getattr(state, "preferred_neutral_topic", ""))
        ):
            state.waiting_for_resume = False
            state.waiting_for_topic_choice = False
            state.preferred_neutral_topic = ""
            state.neutral_topic_turns = 0
            state.repeat_count_for_current_question = 0
            next_q = get_next_redirect_question(state)
            if next_q not in state.asked_questions:
                state.asked_questions.append(next_q)
            area = get_question_behavioral_area(next_q)
            if area and area not in state.asked_areas:
                state.asked_areas.append(area)
            state.questions_asked += 1
            resume_resp = f"Thank you. Let's return to the assessment. {next_q}"
            await send_event_fn(
                ctx,
                "interview_question",
                {
                    "question": resume_resp,
                    "question_number": state.questions_asked,
                },
            )
            spoke_ok = await speak_fn(resume_resp, f"Resumed question #{state.questions_asked}")
            if spoke_ok:
                state.add_transcript("interviewer", resume_resp)
            return True

        # 6b. Check if candidate requests a brief moment (Rule 4)
        if any(w in answer.lower() for w in ("another moment", "a minute", "a moment", "not yet", "give me", "need time", "just a sec")):
            state.waiting_for_resume = True
            topic_resp = "Take all the time you need. Whenever you feel comfortable resuming, just let me know."

        # 6c. Bare acknowledgment like "Okay" (Rule 5: do not treat bare "Okay" as permission to resume)
        elif is_bare_acknowledgment(answer):
            state.waiting_for_resume = True
            state.waiting_for_topic_choice = True
            topic_resp = "Take your time. Please let me know whether you would like me to rephrase the question or move to another relevant interview question."

        # 6d. Initial or repeated request to change topic away from professional life (Rule 4)
        else:
            state.waiting_for_resume = True
            state.waiting_for_topic_choice = True
            state.neutral_topic_turns += 1
            if state.neutral_topic_turns <= 1:
                topic_resp = PROFESSIONAL_TOPIC_REQUEST_RESPONSE
            else:
                topic_resp = (
                    "As a reminder, our interview is dedicated to your professional experience. "
                    "Would you like me to rephrase the current question, or move to another relevant interview question?"
                )

        await send_event_fn(
            ctx,
            "interview_question",
            {
                "question": topic_resp,
                "question_number": state.questions_asked,
            },
        )
        spoke_ok = await speak_fn(topic_resp, "Topic request response")
        if spoke_ok:
            state.add_transcript("interviewer", topic_resp)
        return True

    # 6. CLARIFICATION REQUEST (repeat current question clearly once, avoid endless loops)
    if is_clarification and not disconnect_event.is_set() and not manual_end_event.is_set():
        state.repeat_count_for_current_question += 1
        last_q = get_last_interviewer_question(state)
        clean_q = strip_refusal_prefix(last_q) or "Could you introduce yourself and tell me about your background?"
        if clean_q.lower().startswith("certainly."):
            clean_q = clean_q[len("certainly."):].strip()
        elif clean_q.lower().startswith("to rephrase:"):
            clean_q = clean_q[len("to rephrase:"):].strip()
            if "or if you prefer" in clean_q.lower():
                clean_q = clean_q.split("Or if you prefer")[0].strip()

        if state.repeat_count_for_current_question <= 1:
            clarification_resp = f"Certainly. {clean_q}"
        elif state.repeat_count_for_current_question == 2:
            clarification_resp = f"To rephrase: {clean_q} Or if you prefer, we can move on to a different topic."
        else:
            fallback_q = get_next_redirect_question(state)
            clarification_resp = f"Let's move on to a fresh question: {fallback_q}"
            state.repeat_count_for_current_question = 0

        await send_event_fn(
            ctx,
            "interview_question",
            {
                "question": clarification_resp,
                "question_number": state.questions_asked,
            },
        )
        spoke_ok = await speak_fn(clarification_resp, "Clarification repeat")
        if spoke_ok:
            state.add_transcript("interviewer", clarification_resp.strip())
        return True

    # If paused before analyzing or generating question, wait for resume
    if is_paused_fn and is_paused_fn():
        if resume_event is not None:
            await resume_event.wait()
            if (
                state.completed
                or getattr(state, "is_finalized", False)
                or manual_end_event.is_set()
                or timer_finished_event.is_set()
                or disconnect_event.is_set()
            ):
                return False

    question_transcript = format_transcript(state)
    question_evidence = format_evidence(state)

    ans_key = answer.lower()
    should_analyze_primary = ans_key not in analyzed_answers and not is_non_substantive
    if should_analyze_primary:
        analyzed_answers.add(ans_key)

    print("DEBUG: starting analysis + question generation")
    start_time = time.perf_counter()

    if should_analyze_primary:
        analysis_task = asyncio.create_task(analyze_fn(state, answer))
    else:
        analysis_task = asyncio.create_task(asyncio.sleep(0))

    if is_off_topic:
        print("[Turn Routing] Off-topic branch active: bypassing LLM question generator and using direct spoken redirect.")
        # Immediate off-topic redirection: do not hallucinate about movies or stay silent
        if not is_repeated_off_topic_turn and state.off_topic_count <= 1:
            last_q = get_last_interviewer_question(state)
            clean_last_q = strip_refusal_prefix(last_q)
            if (
                clean_last_q
                and "introduce yourself" not in clean_last_q.lower()
                and not is_unrelated_entertainment_question(clean_last_q)
            ):
                redirect_q = clean_last_q
            else:
                redirect_q = "Could you tell me briefly about your professional background, your role, and the projects or technologies you work with?"
            off_topic_question = f"{OFF_TOPIC_FIRST_PREFIX} {redirect_q}"
        else:
            # Repeated off-topic: pivot to an unasked safe behavioral fallback to prevent looping
            fallback_q = get_next_redirect_question(state)
            off_topic_question = f"{OFF_TOPIC_REPEATED_PREFIX} {fallback_q}"
        question_task = asyncio.create_task(asyncio.sleep(0, result=off_topic_question))
    else:
        print("[Turn Routing] Substantive/contextual branch active: invoking LLM question generator.")
        try:
            import inspect
            sig = inspect.signature(generate_question_fn)
            has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            accepts_refusal = "is_refusal" in sig.parameters or has_kwargs
        except Exception:
            accepts_refusal = True

        gen_kwargs = {
            "current_answer": answer,
            "transcript": question_transcript,
            "evidence": question_evidence,
        }
        if accepts_refusal:
            gen_kwargs["is_refusal"] = is_refusal
            gen_kwargs["refused_question"] = refused_question

        question_task = asyncio.create_task(
            generate_question_fn(
                state,
                remaining_seconds,
                **gen_kwargs,
            )
        )

    _, question = await asyncio.gather(analysis_task, question_task)
    elapsed = time.perf_counter() - start_time
    print(f"DEBUG: analysis + question generation took {elapsed:.2f} seconds")
    if is_abuse:
        print("Candidate abusive language handled; skipped behavioral penalty.")
    elif is_boundary:
        print("Candidate personal/unrelated topic handled; skipped behavioral penalty.")
    elif is_refusal:
        print("Candidate refusal acknowledged; skipped behavioral penalty.")
    elif is_off_topic:
        print("Candidate off-topic answer handled; redirected immediately.")
    else:
        print("Candidate answer analyzed.")

    if is_abuse and question:
        question = format_abuse_warning_response(question, state.abusive_language_count)
    elif is_boundary and question:
        question = format_boundary_response(question)
    elif is_refusal and question:
        question = format_refusal_response(question)

    # Any additional speech turn that completed while LLM was generating belongs to the
    # current question, NOT the next question that hasn't been asked yet.
    if not user_answer_queue.empty() and not disconnect_event.is_set():
        await drain_and_analyze_pending_answers(
            state,
            user_answer_queue,
            analyzed_answers,
            analyze_fn=analyze_fn,
        )

    # If paused before asking the new question, wait until resumed
    if is_paused_fn and is_paused_fn():
        if resume_event is not None:
            await resume_event.wait()
            if (
                state.completed
                or getattr(state, "is_finalized", False)
                or manual_end_event.is_set()
                or timer_finished_event.is_set()
                or disconnect_event.is_set()
            ):
                return False

    if (
        not question
        or warning_sent
        or manual_end_event.is_set()
        or timer_finished_event.is_set()
        or disconnect_event.is_set()
    ):
        if not question and not disconnect_event.is_set() and not manual_end_event.is_set():
            if is_abuse:
                await speak_fn(
                    ABUSE_WARNING_FIRST_PREFIX if state.abusive_language_count <= 1 else ABUSE_WARNING_REPEATED_PREFIX,
                    "Abusive language warning",
                )
            elif is_boundary:
                await speak_fn(
                    PROFESSIONAL_BOUNDARY_PREFIX,
                    "Professional boundary reminder",
                )
            elif is_refusal:
                await speak_fn(
                    "I understand, that is completely fine.",
                    "Refusal acknowledgment",
                )
            elif is_off_topic:
                prefix = OFF_TOPIC_FIRST_PREFIX if state.off_topic_count <= 1 else OFF_TOPIC_REPEATED_PREFIX
                await speak_fn(
                    prefix,
                    "Off-topic redirection",
                )
            else:
                safe_fallback = get_next_redirect_question(state)
                await speak_fn(safe_fallback, "Non-silent safe fallback")
                state.add_transcript("interviewer", safe_fallback)
                state.repeat_count_for_current_question = 0
                if safe_fallback.strip() not in state.asked_questions:
                    state.asked_questions.append(safe_fallback.strip())
                area = get_question_behavioral_area(safe_fallback.strip())
                if area and area not in state.asked_areas:
                    state.asked_areas.append(area)
        return False

    state.questions_asked += 1
    print()
    print("--------------------------------------------")
    print("ADAPTIVE QUESTION")
    print(question)
    print("--------------------------------------------")

    await send_event_fn(
        ctx,
        "interview_question",
        {
            "question": question,
            "question_number": state.questions_asked,
        },
    )

    spoke_ok = await speak_fn(
        question,
        f"Adaptive question #{state.questions_asked}",
    )
    if spoke_ok:
        q_str = question.strip() if isinstance(question, str) else str(question).strip()
        last_q = get_last_interviewer_question(state)
        if last_q != q_str:
            state.add_transcript("interviewer", q_str)
        state.repeat_count_for_current_question = 0
        if q_str and q_str not in state.asked_questions:
            state.asked_questions.append(q_str)
        area = get_question_behavioral_area(q_str)
        if area and area not in state.asked_areas:
            state.asked_areas.append(area)

    if (
        not spoke_ok
        or manual_end_event.is_set()
        or timer_finished_event.is_set()
        or disconnect_event.is_set()
    ):
        return False

    return True


# ============================================================
# PREWARM
# ============================================================

def prewarm(proc: JobProcess):
    """Prewarm the Silero VAD model during worker startup to prevent runtime event-loop blocks."""
    print("[Agent Worker] Prewarming Silero VAD (sample_rate=16000 Hz, min_silence=0.8s)...")
    try:
        proc.userdata["vad"] = silero.VAD.load(
            sample_rate=16000,
            min_silence_duration=0.8,
            activation_threshold=0.5,
        )
        print("[Agent Worker] Silero VAD prewarmed successfully.")
    except Exception as error:
        print("[Agent Worker] Silero VAD prewarm warning:", repr(error))


# ============================================================
# ENTRYPOINT
# ============================================================

async def entrypoint(
    ctx: JobContext,
):

    print()
    print(
        "============================================"
    )
    print(
        "      BEHAVIORAL AI ASSESSMENT AGENT"
    )
    print(
        "============================================"
    )
    print()

    # --------------------------------------------------------
    # CONNECT
    # --------------------------------------------------------

    print(
        "[Agent] [Stage 1/5] Connecting to LiveKit room..."
    )

    await ctx.connect()
    assessment_id = ctx.room.name
    print("AGENT ROOM:", ctx.room.name)
    print("ASSESSMENT ID:", assessment_id)

    print(
        "[Agent] [Stage 1/5] Successfully connected to LiveKit room:",
        ctx.room.name,
    )

    # Listen for audio/video track publication and subscription events
    @ctx.room.on("track_published")
    def on_track_published(publication, participant_obj):
        kind_name = getattr(publication.kind, "name", str(publication.kind))
        print(
            f"[Agent Audio Track] Participant {participant_obj.identity} published track: "
            f"sid={publication.sid}, kind={kind_name}"
        )

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(track, publication, participant_obj):
        kind_name = getattr(track.kind, "name", str(track.kind))
        print(
            f"[Agent Audio Track] Subscribed to track from {participant_obj.identity}: "
            f"sid={track.sid}, kind={kind_name}"
        )

    # --------------------------------------------------------
    # WAIT FOR CANDIDATE
    # --------------------------------------------------------

    print(
        "[Agent] [Stage 2/5] Waiting for candidate participant..."
    )

    participant = await ctx.wait_for_participant()

    print(
        "[Agent] [Stage 2/5] Candidate connected:",
        participant.identity,
    )

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    state = AssessmentState()

    candidate_disconnected = (
        asyncio.Event()
    )

    manual_end_requested = (
        asyncio.Event()
    )

    timer_finished = (
        asyncio.Event()
    )

    is_paused = False
    resume_event = asyncio.Event()
    resume_event.set()
    pause_event = asyncio.Event()

    def _is_paused():
        return is_paused

    warning_sent = False

    accumulated_active_seconds = 0.0
    last_tick_time = time.monotonic()

    def get_remaining_seconds() -> int:
        nonlocal accumulated_active_seconds, last_tick_time
        now = time.monotonic()
        active = accumulated_active_seconds
        if not is_paused:
            active += (now - last_tick_time)
        return max(0, int(ASSESSMENT_DURATION_SECONDS - active))

    # --------------------------------------------------------
    # USER ANSWER QUEUE & TRACKING
    # --------------------------------------------------------

    user_answer_queue = asyncio.Queue()

    processed_transcripts = set()
    analyzed_answers = set()
    uncommitted_final_segments: list[str] = []

    # --------------------------------------------------------
    # PARTICIPANT DISCONNECTED
    # --------------------------------------------------------

    @ctx.room.on(
        "participant_disconnected"
    )
    def on_participant_disconnected(
        disconnected_participant,
    ):

        if (
            disconnected_participant.identity
            == participant.identity
        ):

            print(
                "Candidate disconnected."
            )

            candidate_disconnected.set()

    # --------------------------------------------------------
    # FRONTEND DATA (ROOM-SCOPED & VALIDATED)
    # --------------------------------------------------------

    @ctx.room.on(
        "data_received"
    )
    def on_data_received(packet):
        nonlocal is_paused, accumulated_active_seconds, last_tick_time

        try:

            decoded = packet.data.decode(
                "utf-8"
            )

            payload = json.loads(
                decoded
            )

            print(
                f"[Agent Data] Room '{ctx.room.name}' received data:",
                payload,
            )

            # Room-scoped validation: prevent cross-room message confusion
            packet_room = payload.get("room")
            if packet_room and packet_room != ctx.room.name:
                print(
                    f"[Agent Data] Ignored packet intended for room '{packet_room}' in room '{ctx.room.name}'"
                )
                return

            msg_type = payload.get("type")

            if msg_type == "end_assessment":
                manual_end_requested.set()

            elif msg_type == "pause_interview":
                if not is_paused:
                    now = time.monotonic()
                    accumulated_active_seconds += (now - last_tick_time)
                    last_tick_time = now
                    is_paused = True
                    state.is_paused = True
                    resume_event.clear()
                    pause_event.set()
                    frozen_remaining = get_remaining_seconds()
                    print(
                        f"[Agent Data] Interview paused in room '{ctx.room.name}'. "
                        f"Remaining time frozen at: {frozen_remaining}s"
                    )
                    # Immediately halt active TTS speech
                    if hasattr(session, "interrupt"):
                        try:
                            session.interrupt(force=True)
                        except Exception:
                            pass
                    asyncio.create_task(
                        send_ui_event(
                            ctx,
                            "interview_paused",
                            {
                                "remaining_seconds": frozen_remaining,
                            },
                        )
                    )

            elif msg_type == "resume_interview":
                if is_paused:
                    last_tick_time = time.monotonic()
                    is_paused = False
                    state.is_paused = False
                    pause_event.clear()
                    resume_event.set()
                    current_remaining = get_remaining_seconds()
                    print(
                        f"[Agent Data] Interview resumed in room '{ctx.room.name}'. "
                        f"Remaining time: {current_remaining}s"
                    )
                    asyncio.create_task(
                        send_ui_event(
                            ctx,
                            "interview_resumed",
                            {
                                "remaining_seconds": current_remaining,
                            },
                        )
                    )

        except Exception as error:

            print(
                "Data receive error:",
                repr(error),
            )

    # --------------------------------------------------------
    # SESSION
    # --------------------------------------------------------

    vad = ctx.proc.userdata.get("vad") if hasattr(ctx, "proc") and hasattr(ctx.proc, "userdata") else None
    if vad is None:
        print(
            "[Agent] [Stage 3/5] Loading Silero VAD in background worker thread (sample_rate=16000 Hz, min_silence=0.8s)..."
        )
        vad = await asyncio.to_thread(
            silero.VAD.load,
            sample_rate=16000,
            min_silence_duration=0.8,
            activation_threshold=0.5,
        )
    else:
        print(
            "[Agent] [Stage 3/5] Using prewarmed Silero VAD (sample_rate=16000 Hz, min_silence=0.8s)."
        )

    print(
        "[Agent] [Stage 3/5] Creating AgentSession with uninterrupted speech delivery and 16kHz VAD..."
    )

    speech_lock = asyncio.Lock()
    is_agent_speaking = False

    def _set_speaking_state(val: bool):
        nonlocal is_agent_speaking
        is_agent_speaking = val

    async def speak_safely(text: str, stage_label: str = "Speech", track_speaking_state: bool = True) -> bool:
        return await speak_with_lock(
            session=session,
            text=text,
            speech_lock=speech_lock,
            stage_label=stage_label,
            manual_end_event=manual_end_requested,
            disconnect_event=candidate_disconnected,
            pause_event=pause_event,
            resume_event=resume_event,
            is_paused_fn=_is_paused,
            set_speaking_state=_set_speaking_state if track_speaking_state else None,
        )

    async def handle_completed_candidate_turn(answer_text: str):
        if is_agent_speaking:
            active_q = get_last_interviewer_question(state)
            clean_t = re.sub(r"[^\w\s]", "", (answer_text or "").lower()).strip()
            clean_q = re.sub(r"[^\w\s]", "", active_q.lower()).strip()
            if clean_t and clean_t in clean_q and len(clean_t.split()) >= 2:
                print(f"[Agent Audio] Ignoring speech detected while interviewer was speaking (confirmed acoustic echo): {answer_text[:50]}")
                return
            print(f"[Agent Audio] Candidate speech detected during playback (not an echo): {answer_text[:50]}")

        answer_clean = (answer_text or "").strip()
        accumulated_segments = " ".join(s for s in uncommitted_final_segments if s).strip()
        if accumulated_segments and len(accumulated_segments.split()) > len(answer_clean.split()):
            print(
                f"[Agent Audio] Consolidating turn using accumulated final segments "
                f"({len(accumulated_segments.split())} words vs {len(answer_clean.split())} words)"
            )
            answer_clean = accumulated_segments

        if not answer_clean:
            return
        uncommitted_final_segments.clear()
        answer_key = answer_clean.lower()
        if answer_key in processed_transcripts:
            return
        processed_transcripts.add(answer_key)
        state.add_transcript("candidate", answer_clean)
        if not is_non_introduction_turn(answer_clean, state) and state.questions_asked == 0:
            if not state.candidate_introduction or len(state.candidate_introduction.split()) < len(answer_clean.split()):
                state.candidate_introduction = answer_clean
        await send_ui_event(
            ctx,
            "candidate_answer",
            {
                "answer": answer_clean,
                "is_final": True,
            },
        )
        user_answer_queue.put_nowait(answer_clean)
        print(f"[Agent] Completed candidate turn queued for analysis: {answer_clean[:60]}")

    session = AgentSession(

        stt=deepgram.STT(
            model="nova-3",
            api_key=os.getenv("DEEPGRAM_API_KEY"),
        ),

        tts=deepgram.TTS(
            model="aura-2-asteria-en",
            api_key=os.getenv("DEEPGRAM_API_KEY"),
        ),

        llm=openai.LLM(
            model=OPENAI_MODEL,
            api_key=os.getenv("OPENAI_API_KEY"),
        ),

        vad=vad,

        turn_handling={
            "endpointing": {
                "min_delay": 2.2,
                "max_delay": 5.0,
            },
            "interruption": {
                "enabled": False,
            },
            "preemptive_generation": {
                "enabled": False,
            },
        },
    )

    @session.on("agent_state_changed")
    def on_agent_state_changed(event):
        try:
            print(
                f"[Agent State] State changed: {event.new_state} (previous: {event.old_state})"
            )
        except Exception:
            pass

    @session.on("error")
    def on_session_error(event):
        try:
            print(
                f"[Agent Session Error] {event.error}"
            )
        except Exception:
            pass

    # --------------------------------------------------------
    # USER TRANSCRIPT EVENT (FINALIZED SEGMENT BUFFERING)
    # --------------------------------------------------------

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(event):

        try:

            transcript = getattr(
                event,
                "transcript",
                "",
            )

            is_final = getattr(
                event,
                "is_final",
                False,
            )

            handle_user_input_transcribed_event(
                transcript=transcript,
                is_final=is_final,
                is_agent_speaking=is_agent_speaking,
                uncommitted_final_segments=uncommitted_final_segments,
                send_ui_event_fn=lambda event_type, data: asyncio.create_task(
                    send_ui_event(ctx, event_type, data)
                ),
                active_question=get_last_interviewer_question(state),
            )

        except Exception as error:

            print(
                "Transcript event error:",
                repr(error),
            )

    # --------------------------------------------------------
    # CONVERSATION HISTORY
    # --------------------------------------------------------

    @session.on("conversation_item_added")
    def on_conversation_item_added(event):

        try:

            item = getattr(
                event,
                "item",
                None,
            )

            if item is None:
                return

            text = getattr(
                item,
                "text_content",
                None,
            )

            if not text:
                return

            text = text.strip()

            if not text:
                return

            role = str(
                getattr(
                    item,
                    "role",
                    "unknown",
                )
            )

            print(
                f"CONVERSATION | {role}: {text}"
            )

            if role in (
                "assistant",
                "agent",
            ):

                state.add_transcript(
                    "interviewer",
                    text,
                )

        except Exception as error:

            print(
                "Conversation event error:",
                repr(error),
            )

    # --------------------------------------------------------
    # START SESSION
    # --------------------------------------------------------

    print(
        "[Agent] [Stage 4/5] Starting AgentSession with room..."
    )

    await session.start(
        room=ctx.room,
        agent=BehavioralInterviewAgent(
            on_turn_completed_callback=handle_completed_candidate_turn
        ),
        room_options=room_io.RoomOptions(
            participant_identity=participant.identity,
            video_input=False,
        ),
    )

    published_tracks = [
        f"{pub.sid} (kind={getattr(pub.kind, 'name', pub.kind)}, name={pub.name}, muted={pub.muted})"
        for pub in ctx.room.local_participant.track_publications.values()
    ]
    print(
        f"[Agent] [Stage 4/5] Agent session started. Confirmed published tracks ({len(published_tracks)}): {published_tracks}"
    )

    print(
        "[Agent] [Stage 5/5] Sending assessment_started event to UI..."
    )

    await send_ui_event(
        ctx,
        "assessment_started",
        {
            "duration_seconds":
                ASSESSMENT_DURATION_SECONDS,
        },
    )

    # --------------------------------------------------------
    # OPENING QUESTION
    # --------------------------------------------------------

    opening_question = (
        "To begin, please introduce yourself "
        "and briefly tell me about your background."
    )

    state.questions_asked += 1

    print(
        "[Agent] [Stage 5/5] Sending interview_question event to UI..."
    )

    await send_ui_event(
        ctx,
        "interview_question",
        {
            "question":
                opening_question,
            "question_number":
                state.questions_asked,
        },
    )

    await speak_safely(
        opening_question,
        "Stage 5/5 Opening speech",
    )

    if not any(item.get("text") == opening_question for item in state.transcript):
        state.add_transcript("interviewer", opening_question)
    if opening_question not in state.asked_questions:
        state.asked_questions.append(opening_question)
    state.repeat_count_for_current_question = 0

    # --------------------------------------------------------
    # ASSESSMENT CLOCK
    # --------------------------------------------------------

    assessment_start_time = (
        time.monotonic()
    )
    deadline_at = (
        assessment_start_time
        + ASSESSMENT_DURATION_SECONDS
    )

    async def assessment_clock():
        nonlocal warning_sent, accumulated_active_seconds, last_tick_time
        last_tick_time = time.monotonic()

        while accumulated_active_seconds < ASSESSMENT_DURATION_SECONDS:
            if (
                candidate_disconnected.is_set()
                or manual_end_requested.is_set()
            ):
                return

            await asyncio.sleep(0.5)

            now = time.monotonic()
            delta = now - last_tick_time
            last_tick_time = now

            if not is_paused:
                accumulated_active_seconds += delta

            rem = ASSESSMENT_DURATION_SECONDS - accumulated_active_seconds

            if not warning_sent and rem <= WARNING_SECONDS:
                warning_sent = True

                print(
                    f"{WARNING_SECONDS} seconds remaining."
                )

                await send_ui_event(
                    ctx,
                    "assessment_warning",
                    {
                        "seconds_remaining": max(1, int(rem)),
                    },
                )

                # Wait briefly if the candidate is actively speaking so the warning does not
                # interrupt or discard their active answer.
                while (
                    not candidate_disconnected.is_set()
                    and not manual_end_requested.is_set()
                    and not is_paused
                    and (ASSESSMENT_DURATION_SECONDS - accumulated_active_seconds) > 3.0
                    and (
                        getattr(session, "user_state", None) == "speaking"
                        or bool(uncommitted_final_segments)
                    )
                ):
                    await asyncio.sleep(0.2)

                if (
                    not candidate_disconnected.is_set()
                    and not manual_end_requested.is_set()
                    and not is_paused
                    and (ASSESSMENT_DURATION_SECONDS - accumulated_active_seconds) > 2.0
                ):
                    warning_message = (
                        "Thank you for sharing your responses. "
                        f"We have about {WARNING_SECONDS} seconds remaining in the assessment. "
                        "Please finish your current response. "
                        "I won't start another question. "
                        "Once you're finished, we'll conclude the interview. "
                        "Thank you for your time."
                    )

                    # Do not set is_agent_speaking=True during warning so a candidate finishing
                    # their response is never ignored.
                    await speak_safely(
                        warning_message,
                        "Assessment warning",
                        track_speaking_state=False,
                    )

        timer_finished.set()
        print("Assessment time reached zero.")

    clock_task = asyncio.create_task(
        assessment_clock()
    )

    # --------------------------------------------------------
    # INTERVIEW LOOP
    # --------------------------------------------------------

    answer_task: asyncio.Task | None = None
    rescued_answers: list[str] = []

    try:

        while True:

            if (
                candidate_disconnected.is_set()
                or manual_end_requested.is_set()
                or timer_finished.is_set()
            ):
                break

            if is_paused:
                print("[Agent] Session paused. Waiting for resume...")
                await resume_event.wait()
                if (
                    candidate_disconnected.is_set()
                    or manual_end_requested.is_set()
                    or timer_finished.is_set()
                ):
                    break
                print("[Agent] Session resumed.")

            # ----------------------------------------------
            # WAIT FOR CANDIDATE ANSWER OR EVENTS
            # ----------------------------------------------

            answer_task = asyncio.create_task(
                user_answer_queue.get()
            )

            disconnect_task = asyncio.create_task(
                candidate_disconnected.wait()
            )

            manual_task = asyncio.create_task(
                manual_end_requested.wait()
            )

            timer_task = asyncio.create_task(
                timer_finished.wait()
            )

            pause_task = asyncio.create_task(
                pause_event.wait()
            )

            done, pending = await asyncio.wait(
                {
                    answer_task,
                    disconnect_task,
                    manual_task,
                    timer_task,
                    pause_task,
                },
                return_when=asyncio.FIRST_COMPLETED,
            )

            pending_event_tasks = {
                task
                for task in pending
                if task is not answer_task
            }

            for task in pending_event_tasks:
                task.cancel()

            if pending_event_tasks:
                await asyncio.gather(
                    *pending_event_tasks,
                    return_exceptions=True,
                )

            if pause_task in done:
                # Pause occurred while waiting for candidate answer
                if answer_task in done:
                    try:
                        ans_val = answer_task.result()
                        if isinstance(ans_val, str) and ans_val.strip():
                            user_answer_queue.put_nowait(ans_val.strip())
                    except Exception:
                        pass
                else:
                    answer_task.cancel()
                    try:
                        ans_val = await answer_task
                        if isinstance(ans_val, str) and ans_val.strip():
                            user_answer_queue.put_nowait(ans_val.strip())
                    except (asyncio.CancelledError, Exception):
                        pass
                answer_task = None
                print("[Agent] Waiting for resume after pause...")
                await resume_event.wait()
                if (
                    candidate_disconnected.is_set()
                    or manual_end_requested.is_set()
                    or timer_finished.is_set()
                ):
                    break
                continue

            answer = None
            if answer_task in done:
                try:
                    answer = answer_task.result()
                except Exception:
                    answer = None
                answer_task = None
            else:
                answer_task.cancel()
                try:
                    maybe_ans = await answer_task
                    if isinstance(maybe_ans, str) and maybe_ans.strip():
                        rescued_answers.append(maybe_ans.strip())
                except (asyncio.CancelledError, Exception):
                    pass
                answer_task = None

            if candidate_disconnected.is_set():
                break

            if manual_end_requested.is_set() or timer_finished.is_set():
                if isinstance(answer, str) and answer.strip():
                    rescued_answers.append(answer.strip())
                break

            if not answer or not answer.strip():
                continue

            answer = answer.strip()

            # Turn consolidation debounce: if the candidate answer is short (< 4 words)
            # or the candidate is still actively speaking, wait up to 1.2s for additional speech segments
            # to consolidate instead of cutting off thoughts prematurely.
            consolidated_answer = answer
            try:
                while not user_answer_queue.empty():
                    next_chunk = user_answer_queue.get_nowait()
                    if next_chunk and next_chunk.strip():
                        if not consolidated_answer.lower().endswith(next_chunk.strip().lower()):
                            consolidated_answer = f"{consolidated_answer} {next_chunk.strip()}"

                if len(consolidated_answer.split()) < 4 or getattr(session, "user_state", None) == "speaking":
                    try:
                        next_chunk = await asyncio.wait_for(user_answer_queue.get(), timeout=1.2)
                        if next_chunk and next_chunk.strip():
                            if not consolidated_answer.lower().endswith(next_chunk.strip().lower()):
                                consolidated_answer = f"{consolidated_answer} {next_chunk.strip()}"
                    except asyncio.TimeoutError:
                        pass
            except Exception as e:
                print(f"[Agent Debounce] Debounce consolidation note: {e}")

            answer = consolidated_answer.strip()
            if not is_non_introduction_turn(answer, state) and state.questions_asked == 0:
                if not state.candidate_introduction or len(state.candidate_introduction.split()) < len(answer.split()):
                    state.candidate_introduction = answer

            print()
            print("============================================")
            print("CANDIDATE ANSWER RECEIVED")
            print(answer)
            print("============================================")

            remaining_seconds = get_remaining_seconds()

            should_continue = await process_interview_turn(
                ctx=ctx,
                state=state,
                answer=answer,
                remaining_seconds=remaining_seconds,
                warning_sent=warning_sent,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                manual_end_event=manual_end_requested,
                timer_finished_event=timer_finished,
                disconnect_event=candidate_disconnected,
                speak_fn=speak_safely,
                is_paused_fn=_is_paused,
                resume_event=resume_event,
            )
            if not should_continue:
                break

    except Exception as error:

        print(
            "Interview loop error:",
            repr(error),
        )

    finally:

        # ----------------------------------------------------
        # STOP CLOCK FIRST
        # ----------------------------------------------------

        if not clock_task.done():

            clock_task.cancel()

            try:
                await clock_task
            except asyncio.CancelledError:
                pass

        # ----------------------------------------------------
        # FINALIZE UNCOMMITTED SPEECH & DRAIN PENDING ANSWERS
        # ----------------------------------------------------

        if not candidate_disconnected.is_set():
            await finalize_uncommitted_speech(
                session=session,
                uncommitted_final_segments=uncommitted_final_segments,
                on_turn_callback=handle_completed_candidate_turn,
            )
            await drain_and_analyze_pending_answers(
                state=state,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                answer_task=answer_task,
                extra_answers=rescued_answers,
            )
            answer_task = None
        elif answer_task is not None:
            if not answer_task.done():
                answer_task.cancel()
            try:
                await answer_task
            except (asyncio.CancelledError, Exception):
                pass
            answer_task = None

    # ========================================================
    # CLOSING
    # ========================================================

    if candidate_disconnected.is_set():

        print(
            "Candidate disconnected before closing."
        )

    elif manual_end_requested.is_set():

        print(
            "Manual end requested. Pending answers finalized; proceeding directly to final analysis."
        )

    else:

        closing_message = (
            "Thank you for completing the behavioral assessment. "
            "I appreciate your time and your responses. "
            "The interview is now complete. "
            "Thank you."
        )

        await send_ui_event(
            ctx,
            "assessment_closing",
            {
                "reason":
                    "time_limit",
            },
        )

        await speak_safely(
            closing_message,
            "Closing message",
        )

        await asyncio.sleep(
            CLOSING_MESSAGE_PAUSE_SECONDS
        )

    # ========================================================
    # FINAL ANALYSIS
    # ========================================================

    print(
        "Generating final behavioral analysis..."
    )

    try:
        final_analysis = await generate_final_analysis(
            state
        )

    except Exception as error:

        underlying = getattr(error, "__cause__", None) or error
        error_type = type(underlying).__name__
        error_code = (
            getattr(underlying, "code", None)
            or getattr(underlying, "status_code", None)
            or getattr(underlying, "type", None)
            or error_type
        )
        http_status = getattr(underlying, "status_code", None)
        error_details = str(underlying)
        for secret_pattern in ("sk-", "api_key", "secret", "token"):
            if secret_pattern in error_details.lower():
                error_details = f"Authentication/API error ({error_type}): check provider credentials."
                break

        print(
            f"[Agent] Final assessment generation failed [code: {error_code}, status: {http_status}]: {error_details}"
        )

        state.completed = False

        # Save the interview/transcript even when the final LLM
        # analysis fails. Never replace a failed analysis with
        # a misleading all-zero report.
        await asyncio.to_thread(
            save_assessment,
            state,
            assessment_id,
        )

        await send_ui_event(
            ctx,
            "assessment_error",
            {
                "stage": "final_analysis",
                "code": str(error_code),
                "error_type": error_type,
                "http_status": http_status,
                "message": (
                    "The interview was completed, but the final behavioral analysis could not be generated. "
                    "Your interview data has been saved."
                ),
                "error_details": error_details,
                "recoverable_data": {
                    "assessment_id": assessment_id,
                    "transcript": state.transcript,
                    "questions_asked": state.questions_asked,
                },
            },
        )

        try:
            await asyncio.wait_for(candidate_disconnected.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            pass

        return

    state.final_analysis = final_analysis
    state.completed = True

    # ========================================================
    # DELIVER FINAL RESULT IMMEDIATELY, THEN SAVE & SYNC
    # ========================================================

    await deliver_final_result_and_sync(
        ctx=ctx,
        state=state,
        assessment_id=assessment_id,
        candidate_disconnected=candidate_disconnected,
    )

    print()
    print(
        "============================================"
    )
    print(
        "       ASSESSMENT COMPLETED"
    )
    print(
        "============================================"
    )
    print()

    return



# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import sys

    # Ensure unbuffered stdout so logs stream immediately to Render / console
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    # If no CLI subcommand is provided, default to 'start' for production deployment
    if len(sys.argv) <= 1:
        sys.argv.append("start")

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            port=int(os.environ.get("PORT", "8081")),
            num_idle_processes=0,
            initialize_process_timeout=60.0,
            job_executor_type=JobExecutorType.THREAD,
        )
    )
