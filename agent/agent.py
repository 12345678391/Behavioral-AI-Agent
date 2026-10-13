import asyncio
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

def get_clean_env(key: str) -> str | None:
    val = os.getenv(key)
    if not val:
        return None
    val = val.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val or None

_loaded_env_file = None

for _env_file in ENV_FILES:
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
        _loaded_env_file = _env_file
        break

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

    try:

        response = await openai_client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )

        result = extract_json(
            response.output_text
        )

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

    except Exception as error:

        print(
            "Answer analysis error:",
            repr(error),
        )


# ============================================================
# GENERATE ADAPTIVE QUESTION
# ============================================================

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

    try:

        response = await openai_client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )

        question = (
            response.output_text
            .strip()
        )

        if question:
            return question

    except Exception as error:

        print(
            "Question generation error:",
            repr(error),
        )

    return (
        "Can you tell me about a situation "
        "where you had to solve a difficult problem "
        "and what you personally did?"
    )


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

    def __init__(self):

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

    async def on_user_turn_completed(
        self,
        chat_ctx,
        new_message,
    ):

        # We manually control every interviewer response.
        raise StopResponse()


def process_transcript_event(
    transcript: str,
    is_final: bool,
    state: AssessmentState,
    processed_transcripts: set,
    user_answer_queue: asyncio.Queue,
    send_event_fn,
) -> str | None:
    """Process incoming STT transcript event, separating interim UI updates from final candidate turns."""
    if not transcript:
        return None

    transcript = transcript.strip()
    if not transcript:
        return None

    # Progressive interim speech: emit UI update without mutating interview state or queue
    if not is_final:
        send_event_fn(
            "candidate_interim_transcript",
            {
                "answer": transcript,
                "is_final": False,
            },
        )
        return "interim"

    # Prevent duplicate final transcripts
    transcript_key = transcript.lower()
    if transcript_key in processed_transcripts:
        return "duplicate"

    processed_transcripts.add(transcript_key)

    # Store final candidate answer into state
    state.add_transcript("candidate", transcript)

    if not state.candidate_introduction:
        state.candidate_introduction = transcript

    # Send finalized candidate answer event to React UI
    send_event_fn(
        "candidate_answer",
        {
            "answer": transcript,
            "is_final": True,
        },
    )

    # Put completed answer into interview queue
    user_answer_queue.put_nowait(transcript)
    return "final"


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
    # USER ANSWER QUEUE
    # --------------------------------------------------------

    user_answer_queue = asyncio.Queue()

    processed_transcripts = set()

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

    print(
        "[Agent] [Stage 3/5] Loading VAD model in background worker thread..."
    )

    # Load VAD in a worker thread so the main asyncio event loop is not blocked during ONNX initialization
    vad = await asyncio.to_thread(silero.VAD.load)

    print(
        "[Agent] [Stage 3/5] VAD model loaded successfully. Creating AgentSession..."
    )

    session = AgentSession(

        stt=deepgram.STT(
            model="nova-3",
        ),

        tts=deepgram.TTS(
            model="aura-2-asteria-en",
        ),

        llm=openai.LLM(
            model=OPENAI_MODEL,
        ),

        vad=vad,

        turn_handling={
            "interruption": {
                "mode": "vad"
            }
        },
    )

    # --------------------------------------------------------
    # USER TRANSCRIPT EVENT
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

            def _send_event(event_type: str, data: dict):
                asyncio.create_task(
                    send_ui_event(
                        ctx,
                        event_type,
                        data,
                    )
                )

            action = process_transcript_event(
                transcript,
                is_final,
                state,
                processed_transcripts,
                user_answer_queue,
                _send_event,
            )

            if action == "final":
                print(
                    "Candidate answer added to interview queue."
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

            # Candidate messages are already stored
            # by user_input_transcribed.
            #
            # Only store interviewer messages here.

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
        agent=BehavioralInterviewAgent(),
    )

    print(
        "[Agent] [Stage 4/5] Agent session started. Audio track publication initiated."
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

    print(
        "[Agent] [Stage 5/5] Requesting opening speech delivery via TTS..."
    )

    speech_handle = session.say(
        opening_question
    )

    print(
        f"[Agent] [Stage 5/5] Opening speech queued (speech_id: {speech_handle.id}). Awaiting speech playout..."
    )

    await speech_handle

    speech_error = speech_handle.exception()
    if speech_error:
        print(
            f"[Agent] [Stage 5/5] ERROR: Opening speech TTS/playout FAILED (speech_id: {speech_handle.id}): {speech_error!r}"
        )
    elif speech_handle.interrupted:
        print(
            f"[Agent] [Stage 5/5] Opening speech was interrupted by candidate before completion (speech_id: {speech_handle.id})."
        )
    else:
        print(
            f"[Agent] [Stage 5/5] Opening speech playout completed without errors (speech_id: {speech_handle.id})."
        )

    # --------------------------------------------------------
    # ASSESSMENT CLOCK
    # --------------------------------------------------------

    assessment_start_time = (
        time.monotonic()
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

        warning_message = (
           "Thank you for sharing your responses. "
           f"We have about {WARNING_SECONDS} seconds remaining in the assessment. "
           "Please finish your current response. "
           "I won't start another question. "
           "Once you're finished, we'll conclude the interview. "
           "Thank you for your time."
        )

        await session.say(
            warning_message
        )

        final_wait = (
            ASSESSMENT_DURATION_SECONDS
            - WARNING_DELAY_SECONDS
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

    answer_task = asyncio.create_task(
    user_answer_queue.get()
    )

    try:

        while True:

            if (
                candidate_disconnected.is_set()
            ):
                break

            if (
                manual_end_requested.is_set()
            ):
                break

            if (
                timer_finished.is_set()
            ):
                break

            # ----------------------------------------------
            # WAIT FOR CANDIDATE ANSWER
            # ----------------------------------------------

            

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

            # ----------------------------------------------
            # CANDIDATE DISCONNECTED
            # ----------------------------------------------

            if candidate_disconnected.is_set():
                break

            # ----------------------------------------------
            # MANUAL END
            # ----------------------------------------------

            if manual_end_requested.is_set():
                break

            # ----------------------------------------------
            # TIMER
            # ----------------------------------------------

            if timer_finished.is_set():
                break

            # ----------------------------------------------
            # ANSWER
            # ----------------------------------------------

            if answer_task in done:

                try:

                    answer = (
                        answer_task.result()
                    )

                except Exception:

                    answer = None

                finally:

                    answer_task = asyncio.create_task(
                        user_answer_queue.get()
                    )

                if not answer:
                    continue

                answer = answer.strip()

                if not answer:
                    continue

                print()
                print(
                    "============================================"
                )
                print(
                    "CANDIDATE ANSWER RECEIVED"
                )
                print(
                    answer
                )
                print(
                    "============================================"
                )

                # ------------------------------------------
                # FIRST ANSWER = INTRODUCTION
                # ------------------------------------------

                if (
                    not state.candidate_introduction
                ):

                    state.candidate_introduction = (
                        answer
                    )

                # ------------------------------------------
                # DON'T ASK AFTER WARNING
                # ------------------------------------------

                if warning_sent:

                    print(
                        "Candidate finished current response after warning."
                    )

                    break

                # ------------------------------------------
                # ACTUAL REMAINING TIME
                # ------------------------------------------

                elapsed = (
                    time.monotonic()
                    - assessment_start_time
                )

                remaining_seconds = max(
                    1,
                    int(
                        ASSESSMENT_DURATION_SECONDS
                        - elapsed
                    ),
                )

                if (
                    remaining_seconds
                    <= WARNING_SECONDS
                ):
                    break

                # ------------------------------------------
                # ANALYZE ANSWER + GENERATE QUESTION
                # IN PARALLEL
                # ------------------------------------------

                # Snapshot the context before analysis mutates
                # state.evidence. The latest answer is passed
                # explicitly to the question generator.
                question_transcript = format_transcript(
                    state
                )

                question_evidence = format_evidence(
                    state
                )

                print(
                    "DEBUG: starting analysis + question generation"
                )

                start_time = time.perf_counter()

                analysis_task = asyncio.create_task(
                    analyze_candidate_answer(
                        state,
                        answer,
                    )
                )

                question_task = asyncio.create_task(
                    generate_adaptive_question(
                        state,
                        remaining_seconds,
                        current_answer=answer,
                        transcript=question_transcript,
                        evidence=question_evidence,
                    )
                )

                analysis_result, question = await asyncio.gather(
                    analysis_task,
                    question_task,
                )

                elapsed = time.perf_counter() - start_time

                print(
                    f"DEBUG: analysis + question generation took {elapsed:.2f} seconds"
                )

                print(
                    "Candidate answer analyzed."
                )

                # ------------------------------------------
                # NEXT ADAPTIVE QUESTION
                # ------------------------------------------

                if not question:
                    break

                # ------------------------------------------
                # SAFETY CHECK
                # ------------------------------------------

                if warning_sent:
                    break

                if (
                    manual_end_requested.is_set()
                    or timer_finished.is_set()
                    or candidate_disconnected.is_set()
                ):
                    break

                # ------------------------------------------
                # ASK NEXT QUESTION
                # ------------------------------------------

                state.questions_asked += 1

                print()
                print(
                    "--------------------------------------------"
                )
                print(
                    "ADAPTIVE QUESTION"
                )
                print(
                    question
                )
                print(
                    "--------------------------------------------"
                )

                await send_ui_event(
                    ctx,
                    "interview_question",
                    {
                        "question":
                            question,
                        "question_number":
                            state.questions_asked,
                    },
                )

                print(
                    f"[Agent] Requesting adaptive question speech delivery via TTS (question #{state.questions_asked})..."
                )

                adaptive_handle = session.say(
                    question
                )

                print(
                    f"[Agent] Adaptive question queued (speech_id: {adaptive_handle.id}). Awaiting speech playout..."
                )

                await adaptive_handle

                adaptive_error = adaptive_handle.exception()
                if adaptive_error:
                    print(
                        f"[Agent] ERROR: Adaptive question speech FAILED (speech_id: {adaptive_handle.id}): {adaptive_error!r}"
                    )
                elif adaptive_handle.interrupted:
                    print(
                        f"[Agent] Adaptive question speech was interrupted by candidate (speech_id: {adaptive_handle.id})."
                    )
                else:
                    print(
                        f"[Agent] Adaptive question speech playout completed without errors (speech_id: {adaptive_handle.id})."
                    )

    except Exception as error:

        print(
            "Interview loop error:",
            repr(error),
        )

    finally:

        # ----------------------------------------------------
        # STOP ANSWER TASK
        # ----------------------------------------------------

        if not answer_task.done():
            answer_task.cancel()

        try:
            await answer_task
        except asyncio.CancelledError:
            pass

        # ----------------------------------------------------
        # STOP CLOCK
        # ----------------------------------------------------

        if not clock_task.done():

            clock_task.cancel()

            try:
                await clock_task
            except asyncio.CancelledError:
                pass

    # ========================================================
    # CLOSING
    # ========================================================

    if candidate_disconnected.is_set():

        print(
            "Candidate disconnected before closing."
        )

    elif manual_end_requested.is_set():

        # IMPORTANT:
        # Do not send assessment_closing or wait for a spoken
        # closing message here. The frontend may treat the
        # closing event as a signal to disconnect the LiveKit
        # room. We must generate and publish final_result first.
        print(
            "Manual end requested. Proceeding directly to final analysis."
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

        await session.say(
            closing_message
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
        save_assessment(
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

        return

    state.final_analysis = final_analysis
    state.completed = True

    # ========================================================
    # SAVE
    # ========================================================

    save_assessment(
        state,
        assessment_id,
    )

    # ========================================================
    # SEND FINAL RESULT TO FRONTEND
    # ========================================================

    try:
        await send_ui_event(
            ctx,
            "final_result",
            state.to_dict(),
        )
        print(
            "FINAL RESULT SENT TO FRONTEND."
        )
    except Exception as error:
        print(
            "Final result send failed:",
            repr(error),
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
            port=int(os.environ.get("PORT", "8081")),
            num_idle_processes=0,
            initialize_process_timeout=60.0,
            job_executor_type=JobExecutorType.THREAD,
        )
    )