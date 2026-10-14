import asyncio
import hashlib
import hmac
import json
import os
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

    final_analysis: Dict = field(
        default_factory=dict
    )

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

            "questions_asked":
                self.questions_asked,

            "completed":
                self.completed,

            "final_analysis":
                self.final_analysis,
        }


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

async def generate_adaptive_question(
    state: AssessmentState,
    remaining_seconds: int,
    current_answer: str = "",
    transcript: str | None = None,
    evidence: str | None = None,
) -> str:

    # Capture the adaptive-question context before the analysis task
    # mutates state.evidence. The current answer is passed explicitly
    # so the question generator can use the latest response even while
    # evidence analysis runs in parallel.
    if transcript is None:
        transcript = format_transcript(state)

    if evidence is None:
        evidence = format_evidence(state)

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

The candidate's previous conversation:

{transcript}

Current behavioral evidence:

{evidence}

Candidate's latest answer:

{current_answer}

Time remaining:

{remaining_seconds} seconds.

Generate ONE natural follow-up question.

Important:

- Do NOT follow a fixed scenario list.
- Do NOT ask random generic questions.
- Choose the next question based specifically on the candidate's previous response.
- Identify behavioral areas where stronger evidence is useful.
- Create a realistic workplace situation when appropriate.
- Ask what the candidate would do or what they actually did.
- Ask for a concrete example when useful.
- Avoid repeating something already answered.
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
            return raw_question

    # Diverse fallback selection ensuring no question is duplicated
    asked_questions = {
        item.get("text", "").lower().strip()
        for item in state.transcript
        if item.get("role") in ("interviewer", "assistant", "agent")
    }

    for fallback in FALLBACK_BEHAVIORAL_QUESTIONS:
        if fallback.lower().strip() not in asked_questions:
            return fallback

    return FALLBACK_BEHAVIORAL_QUESTIONS[0]


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
) -> bool:
    """Handle a user transcript event from STT.

    If speech arrives while agent is speaking, or transcript is empty, ignore it.
    If final: record segment to uncommitted_final_segments for turn fallback.
    If interim: dispatch candidate_interim_transcript over UI data channel without modifying
    permanent transcript or candidate answer queue.
    Returns True if an event was handled, False if ignored.
    """
    if not transcript or is_agent_speaking:
        return False

    transcript = transcript.strip()
    if not transcript:
        return False

    print(f"USER TRANSCRIPT | final={is_final}: {transcript}")

    if is_final:
        uncommitted_final_segments.append(transcript)
    else:
        if send_ui_event_fn is not None:
            send_ui_event_fn(
                "candidate_interim_transcript",
                {
                    "answer": transcript,
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
        if not state.candidate_introduction:
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
) -> bool:
    """Serialize session.say calls with speech_lock and handle End Assessment / disconnect cleanly during playback."""
    if (manual_end_event and manual_end_event.is_set()) or (
        disconnect_event and disconnect_event.is_set()
    ):
        return False

    async with speech_lock:
        if (manual_end_event and manual_end_event.is_set()) or (
            disconnect_event and disconnect_event.is_set()
        ):
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

            done, _ = await asyncio.wait(
                wait_set,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if playout_task not in done:
                print(f"[Agent] [{stage_label}] Stopping active speech due to assessment end/disconnect.")
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
) -> bool:
    """Analyze candidate answer, drain any continuation turns arriving during LLM generation, and ask next question if active."""
    answer = (answer or "").strip()
    if not answer:
        return True

    if not state.candidate_introduction:
        state.candidate_introduction = answer

    # If warning or end condition already reached, analyze this final valid answer before stopping question generation
    if (
        warning_sent
        or remaining_seconds <= WARNING_SECONDS
        or manual_end_event.is_set()
        or timer_finished_event.is_set()
    ):
        if warning_sent:
            print("Candidate finished current response after warning.")
        if not disconnect_event.is_set():
            await drain_and_analyze_pending_answers(
                state,
                user_answer_queue,
                analyzed_answers,
                extra_answers=[answer],
                analyze_fn=analyze_fn,
            )
        return False

    question_transcript = format_transcript(state)
    question_evidence = format_evidence(state)

    ans_key = answer.lower()
    should_analyze_primary = ans_key not in analyzed_answers
    if should_analyze_primary:
        analyzed_answers.add(ans_key)

    print("DEBUG: starting analysis + question generation")
    start_time = time.perf_counter()

    if should_analyze_primary:
        analysis_task = asyncio.create_task(analyze_fn(state, answer))
    else:
        analysis_task = asyncio.create_task(asyncio.sleep(0))

    question_task = asyncio.create_task(
        generate_question_fn(
            state,
            remaining_seconds,
            current_answer=answer,
            transcript=question_transcript,
            evidence=question_evidence,
        )
    )

    _, question = await asyncio.gather(analysis_task, question_task)
    elapsed = time.perf_counter() - start_time
    print(f"DEBUG: analysis + question generation took {elapsed:.2f} seconds")
    print("Candidate answer analyzed.")

    # Any additional speech turn that completed while LLM was generating belongs to the
    # current question, NOT the next question that hasn't been asked yet.
    if not user_answer_queue.empty() and not disconnect_event.is_set():
        await drain_and_analyze_pending_answers(
            state,
            user_answer_queue,
            analyzed_answers,
            analyze_fn=analyze_fn,
        )

    if (
        not question
        or warning_sent
        or manual_end_event.is_set()
        or timer_finished_event.is_set()
        or disconnect_event.is_set()
    ):
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
    print("[Agent Worker] Prewarming Silero VAD (sample_rate=16000 Hz)...")
    try:
        proc.userdata["vad"] = silero.VAD.load(sample_rate=16000)
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

    warning_sent = False

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
    # FRONTEND DATA
    # --------------------------------------------------------

    @ctx.room.on(
        "data_received"
    )
    def on_data_received(packet):

        try:

            decoded = packet.data.decode(
                "utf-8"
            )

            payload = json.loads(
                decoded
            )

            print(
                "Browser data:",
                payload,
            )

            if (
                payload.get("type")
                == "end_assessment"
            ):

                manual_end_requested.set()

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
            "[Agent] [Stage 3/5] Loading Silero VAD in background worker thread (sample_rate=16000 Hz)..."
        )
        vad = await asyncio.to_thread(silero.VAD.load, sample_rate=16000)
    else:
        print(
            "[Agent] [Stage 3/5] Using prewarmed Silero VAD (sample_rate=16000 Hz)."
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
            set_speaking_state=_set_speaking_state if track_speaking_state else None,
        )

    async def handle_completed_candidate_turn(answer_text: str):
        if is_agent_speaking:
            print(f"[Agent] Ignoring speech detected while interviewer was speaking (acoustic echo): {answer_text[:50]}")
            return
        answer_clean = (answer_text or "").strip()
        if not answer_clean:
            return
        uncommitted_final_segments.clear()
        answer_key = answer_clean.lower()
        if answer_key in processed_transcripts:
            return
        processed_transcripts.add(answer_key)
        state.add_transcript("candidate", answer_clean)
        if not state.candidate_introduction:
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
                "min_delay": 1.2,
                "max_delay": 3.0,
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

        nonlocal warning_sent

        warning_at = (
            assessment_start_time
            + WARNING_DELAY_SECONDS
        )

        remaining = (
            warning_at
            - time.monotonic()
        )

        if remaining > 0:

            await asyncio.sleep(
                remaining
            )

        if (
            candidate_disconnected.is_set()
            or manual_end_requested.is_set()
        ):
            return

        warning_sent = True

        print(
            f"{WARNING_SECONDS} seconds remaining."
        )

        await send_ui_event(
            ctx,
            "assessment_warning",
            {
                "seconds_remaining":
                    WARNING_SECONDS,
            },
        )

        # Wait briefly if the candidate is actively speaking so the warning does not
        # interrupt or discard their active answer.
        while (
            not candidate_disconnected.is_set()
            and not manual_end_requested.is_set()
            and time.monotonic() < deadline_at - 3.0
            and (
                getattr(session, "user_state", None) == "speaking"
                or bool(uncommitted_final_segments)
            )
        ):
            await asyncio.sleep(0.2)

        if (
            not candidate_disconnected.is_set()
            and not manual_end_requested.is_set()
            and time.monotonic() < deadline_at - 2.0
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

        final_wait = (
            deadline_at
            - time.monotonic()
        )

        if final_wait > 0:

            await asyncio.sleep(
                final_wait
            )

        timer_finished.set()

        print(
            "Assessment time reached zero."
        )

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

            # ----------------------------------------------
            # WAIT FOR CANDIDATE ANSWER
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

            done, pending = await asyncio.wait(
                {
                    answer_task,
                    disconnect_task,
                    manual_task,
                    timer_task,
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

            print()
            print("============================================")
            print("CANDIDATE ANSWER RECEIVED")
            print(answer)
            print("============================================")

            elapsed = time.monotonic() - assessment_start_time
            remaining_seconds = max(
                1,
                int(ASSESSMENT_DURATION_SECONDS - elapsed),
            )

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