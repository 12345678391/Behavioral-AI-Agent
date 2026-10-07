import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List

from dotenv import load_dotenv
from config import (
    ASSESSMENT_DURATION_SECONDS,
    WARNING_SECONDS,
    OPENAI_MODEL,
    BEHAVIORAL_AREAS,
)
from openai import AsyncOpenAI

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
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


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv(".env.local")


# ============================================================
# ASSESSMENT SETTINGS
# ============================================================

WARNING_DELAY_SECONDS = (
    ASSESSMENT_DURATION_SECONDS - WARNING_SECONDS
)

RESULT_FILE = "latest_assessment.json"


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
    api_key=os.getenv("OPENAI_API_KEY")
)


# ============================================================
# SAVE RESULT
# ============================================================

def save_assessment(
    state: AssessmentState,
):
    path = os.path.join(
        os.path.dirname(__file__),
        RESULT_FILE,
    )

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

def format_evidence(
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
# FORMAT TRANSCRIPT
# ============================================================

def format_transcript(
    state: AssessmentState,
) -> str:

    if not state.transcript:
        return "No transcript available."

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
) -> str:

    transcript = format_transcript(
        state
    )

    evidence = format_evidence(
        state
    )

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

    transcript = format_transcript(
        state
    )

    evidence = format_evidence(
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

    try:

        response = await openai_client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )

        result = extract_json(
            response.output_text
        )

        if result:

            state.final_analysis = result

            return result

    except Exception as error:

        print(
            "Final analysis error:",
            repr(error),
        )

    state.final_analysis = {
        "scores": {
            area: 0
            for area in BEHAVIORAL_AREAS
        },
        "overall_score": 0,
        "overall_communication":
            "Insufficient assessment data was available.",
        "strengths": [],
        "areas_for_improvement": [],
        "behavioral_summary":
            "The assessment did not collect enough evidence to generate a complete behavioral summary.",
        "team_working_style": {
            "collaboration": 0,
            "independence": 0,
            "adaptability": 0,
            "leadership": 0,
            "structure": 0,
            "autonomy": 0,
            "summary":
                "Insufficient evidence.",
            "environment_alignment": [],
        },
    }

    return state.final_analysis


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

    await ctx.connect()

    print(
        "Connected to LiveKit."
    )

    # --------------------------------------------------------
    # WAIT FOR CANDIDATE
    # --------------------------------------------------------

    participant = await ctx.wait_for_participant()

    print(
        "Candidate connected:",
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
    # AGENT SESSION
    # --------------------------------------------------------

    session = AgentSession(
        stt=deepgram.STT(
            model="nova-3"
        ),
        tts=deepgram.TTS(
            model="aura-2-asteria-en"
        ),
        llm=openai.LLM(
            model=OPENAI_MODEL
        ),
        vad=silero.VAD.load(),
        turn_handling={
            "interruption": {
                "mode": "vad"
            }
        }
    )


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
    @ctx.room.on("data_received")
    def on_data_received(data: rtc.DataPacket):
      print(
        "DATA RECEIVED CALLBACK FIRED:",
        type(data),
    )

    try:
        raw_data = getattr(data, "data", None)

        print(
            "RAW DATA:",
            raw_data,
        )

        if raw_data is None:
            print(
                "Data packet has no data field."
            )
            return

        decoded = raw_data.decode("utf-8")

        print(
            "DECODED DATA:",
            decoded,
        )

        payload = json.loads(decoded)

        print(
            "Browser data:",
            payload,
        )

        if payload.get("type") == "end_assessment":
            print(
                "Manual end request received from browser."
            )

            manual_end_requested.set()

    except Exception as error:
        print(
            "Data receive error:",
            repr(error),
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

            if not transcript:
                return

            transcript = transcript.strip()

            print(
                f"USER TRANSCRIPT | final={is_final}: {transcript}"
            )

            # Ignore partial/interim speech.
            if not is_final:
                return

            # Prevent duplicate final transcripts.
            transcript_key = transcript.lower()

            if transcript_key in processed_transcripts:
                return

            processed_transcripts.add(
                transcript_key
            )

            # Store candidate answer.
            state.add_transcript(
                "candidate",
                transcript,
            )

            # First answer = introduction.
            if not state.candidate_introduction:
                state.candidate_introduction = transcript

            # Send candidate answer to React.
            asyncio.create_task(
                send_ui_event(
                    ctx,
                    "candidate_answer",
                    {
                        "answer": transcript,
                    },
                )
            )

            # Put completed answer into interview queue.
            user_answer_queue.put_nowait(
                transcript
            )

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

    await session.start(
        room=ctx.room,
        agent=BehavioralInterviewAgent(),
    )

    print(
        "Agent session started."
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

    await session.say(
        opening_question
    )

    print(
        "Opening question delivered."
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
            "We have about 40 seconds remaining in the assessment. "
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

            tasks = {
                answer_task,
                disconnect_task,
                manual_task,
                timer_task,
            }

            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()

            if pending:
                await asyncio.gather(
                    *pending,
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
                print(
                    "Manual end requested."
                )
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

                    continue

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
                # ANALYZE ANSWER
                # ------------------------------------------

                await analyze_candidate_answer(
                    state,
                    answer,
                )

                print(
                    "Candidate answer analyzed."
                )

                # ------------------------------------------
                # GENERATE NEXT ADAPTIVE QUESTION
                # ------------------------------------------

                question = (
                    await generate_adaptive_question(
                        state,
                        remaining_seconds,
                    )
                )

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

                await session.say(
                    question
                )

    except Exception as error:

        print(
            "Interview loop error:",
            repr(error),
        )

    finally:

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

        print(
            "Starting manual assessment closing."
        )

        closing_message = (
            "Thank you for taking the time to complete "
            "the behavioral assessment. "
            "I appreciate your responses. "
            "The interview is now complete. "
            "Thank you, and have a great day."
        )

        await send_ui_event(
            ctx,
            "assessment_closing",
            {
                "reason":
                    "manual_end",
            },
        )

        await session.say(
            closing_message
        )

        await asyncio.sleep(
            3
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
            3
        )

    # ========================================================
    # FINAL ANALYSIS
    # ========================================================

    print(
        "Generating final behavioral analysis..."
    )

    final_analysis = (
        await generate_final_analysis(
            state
        )
    )

    state.final_analysis = (
        final_analysis
    )

    state.completed = True

    # ========================================================
    # SAVE
    # ========================================================

    save_assessment(
        state
    )

    # ========================================================
    # SEND FINAL RESULT TO FRONTEND
    # ========================================================

    print(
        "Sending final_result to frontend..."
    )

    await send_ui_event(
        ctx,
        "final_result",
        state.to_dict(),
    )

    print(
        "final_result sent."
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

    await asyncio.sleep(
        3
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint
        )
    )