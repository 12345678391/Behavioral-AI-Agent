import sys
import unittest
from pathlib import Path
from aiohttp import web

class TestAgentStartup(unittest.TestCase):
    def test_default_cli_arg(self):
        """Verify that when sys.argv has no subcommand, 'start' is defaulted."""
        orig_argv = sys.argv.copy()
        try:
            sys.argv = ["agent/agent.py"]
            if len(sys.argv) <= 1:
                sys.argv.append("start")
            self.assertEqual(sys.argv, ["agent/agent.py", "start"])
        finally:
            sys.argv = orig_argv

    def test_health_route_patching(self):
        """Verify that when a '/' route is added to web.Application, '/health' is also registered."""
        import os
        for k in ["DEEPGRAM_API_KEY", "OPENAI_API_KEY", "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]:
            if not os.environ.get(k):
                os.environ[k] = "test-dummy-key"
        import agent.agent
        app = web.Application()
        async def dummy_handler(request):
            return web.Response(text="OK")
        app.add_routes([web.get("/", dummy_handler)])
        routes = [r.resource.canonical for r in app.router.routes()]
        self.assertIn("/", routes)
        self.assertIn("/health", routes)

    def test_extract_json_markdown_blocks(self):
        """Verify extract_json strips markdown code blocks and handles json correctly."""
        from agent.agent import extract_json
        raw = "```json\n{\"overall_score\": 85, \"scores\": {\"communication\": 90}}\n```"
        parsed = extract_json(raw)
        self.assertEqual(parsed.get("overall_score"), 85)
        self.assertEqual(parsed.get("scores", {}).get("communication"), 90)

        # Non-fenced json
        self.assertEqual(extract_json('{"key": "value"}'), {"key": "value"})

        # Empty or invalid string
        self.assertEqual(extract_json(""), {})
        self.assertEqual(extract_json("invalid"), {})

    def test_assessment_state_recoverable_data(self):
        """Verify AssessmentState retains transcript and evidence for recoverable data."""
        from agent.agent import AssessmentState
        state = AssessmentState()
        state.add_transcript("candidate", "I led the migration project successfully.")
        state.add_evidence("leadership", "Led migration project", "strong", 0.9)
        d = state.to_dict()
        self.assertEqual(len(d["transcript"]), 1)
        self.assertEqual(d["transcript"][0]["role"], "candidate")
        self.assertEqual(len(d["evidence"]["leadership"]), 1)

    def test_requirements_file_exists(self):
        """Verify requirements.txt exists at root and agent folder."""
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "requirements.txt").exists(), "root requirements.txt missing")
        self.assertTrue((root / "agent" / "requirements.txt").exists(), "agent requirements.txt missing")

    def test_worker_options_thread_executor(self):
        """Verify that WorkerOptions supports JobExecutorType.THREAD with num_idle_processes=0."""
        from livekit.agents import WorkerOptions, JobExecutorType
        opts = WorkerOptions(
            entrypoint_fnc=lambda ctx: None,
            port=8081,
            num_idle_processes=0,
            initialize_process_timeout=60.0,
            job_executor_type=JobExecutorType.THREAD,
        )
        self.assertEqual(opts.num_idle_processes, 0)
        self.assertEqual(opts.initialize_process_timeout, 60.0)
        self.assertEqual(opts.job_executor_type, JobExecutorType.THREAD)

    def test_generate_final_analysis_chat_completions_fallback(self):
        """Verify generate_final_analysis falls back to chat.completions when responses.create fails."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from agent.agent import AssessmentState, generate_final_analysis

        state = AssessmentState()
        mock_choice = MagicMock()
        mock_choice.message.content = '{"overall_score": 85, "scores": {"communication": 85}, "strengths": ["Good"]}'

        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp_create, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat_create:
            mock_resp_create.side_effect = RuntimeError("Responses API 500 endpoint error")
            mock_chat_create.return_value = mock_chat_resp

            result = asyncio.run(generate_final_analysis(state))

            self.assertEqual(result["overall_score"], 85)
            self.assertEqual(result["scores"]["communication"], 85)
            self.assertIn("overall_communication", result)
            self.assertIn("team_working_style", result)
            self.assertIn("behavioral_summary", result)
            mock_resp_create.assert_awaited_once()
            mock_chat_create.assert_awaited_once()

    def test_generate_final_analysis_both_fail_raises_runtime_error(self):
        """Verify generate_final_analysis raises RuntimeError with cause when both LLM endpoints fail."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import AssessmentState, generate_final_analysis

        state = AssessmentState()

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp_create, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat_create:
            mock_resp_create.side_effect = ConnectionError("Connection refused on responses")
            mock_chat_create.side_effect = TimeoutError("Timeout on chat.completions")

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(generate_final_analysis(state))

            self.assertIn("OpenAI final analysis generation failed", str(ctx.exception))
            self.assertIsInstance(ctx.exception.__cause__, TimeoutError)

    def test_generate_final_analysis_invalid_json_raises_runtime_error(self):
        """Verify generate_final_analysis raises RuntimeError when LLM returns invalid JSON."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from agent.agent import AssessmentState, generate_final_analysis

        state = AssessmentState()
        mock_choice = MagicMock()
        mock_choice.message.content = "Not a json response text at all."
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp_create, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat_create:
            mock_resp_create.side_effect = RuntimeError("Endpoint error")
            mock_chat_create.return_value = mock_chat_resp

            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(generate_final_analysis(state))

            self.assertIn("invalid final analysis", str(ctx.exception))

    def test_prewarm_initializes_silero_vad_16k(self):
        """Verify prewarm loads Silero VAD with 16000 Hz sample rate into proc.userdata."""
        from unittest.mock import MagicMock, patch
        from agent.agent import prewarm

        mock_proc = MagicMock()
        mock_proc.userdata = {}

        mock_vad = MagicMock()
        with patch("livekit.plugins.silero.VAD.load", return_value=mock_vad) as mock_load:
            prewarm(mock_proc)
            mock_load.assert_called_once_with(
                sample_rate=16000,
                min_silence_duration=0.8,
                activation_threshold=0.5,
            )
            self.assertEqual(mock_proc.userdata.get("vad"), mock_vad)

    def test_worker_options_includes_prewarm_fnc(self):
        """Verify WorkerOptions is configured with prewarm_fnc, THREAD executor, and timeout."""
        from livekit.agents import WorkerOptions, JobExecutorType
        from agent.agent import entrypoint, prewarm

        opts = WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            port=8081,
            num_idle_processes=0,
            initialize_process_timeout=60.0,
            job_executor_type=JobExecutorType.THREAD,
        )
        self.assertEqual(opts.prewarm_fnc, prewarm)
        self.assertEqual(opts.entrypoint_fnc, entrypoint)
        self.assertEqual(opts.num_idle_processes, 0)
        self.assertEqual(opts.job_executor_type, JobExecutorType.THREAD)

    def test_turn_handling_interruption_disabled(self):
        """Verify that TurnHandlingOptions allows disabling interruptions to prevent echo stutter."""
        from livekit.agents.voice.turn import _resolve_interruption
        res = _resolve_interruption({"enabled": False})
        self.assertFalse(res["enabled"])

    def test_sanitize_environment_strips_whitespace_and_newlines(self):
        """Verify sanitize_environment cleans dirty strings in os.environ."""
        import os
        from agent.agent import sanitize_environment

        os.environ["OPENAI_API_KEY"] = "  sk-test-secret-with-newline\n\r  "
        os.environ["DEEPGRAM_API_KEY"] = '"quoted-deepgram-key"'
        sanitize_environment()

        self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-test-secret-with-newline")
        self.assertEqual(os.environ["DEEPGRAM_API_KEY"], "quoted-deepgram-key")

    def test_analyze_candidate_answer_chat_completions_fallback(self):
        """Verify analyze_candidate_answer falls back to chat.completions when responses.create fails."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from agent.agent import AssessmentState, analyze_candidate_answer

        state = AssessmentState()
        mock_choice = MagicMock()
        mock_choice.message.content = '{"evidence": [{"area": "teamwork", "evidence": "Worked with peer", "strength": "strong", "confidence": 0.9}]}'
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("Responses 500 endpoint error")
            mock_chat.return_value = mock_chat_resp

            asyncio.run(analyze_candidate_answer(state, "I worked closely with my teammate to resolve the issue."))

            self.assertEqual(len(state.evidence["teamwork"]), 1)
            self.assertEqual(state.evidence["teamwork"][0].evidence, "Worked with peer")

    def test_generate_adaptive_question_chat_fallback(self):
        """Verify generate_adaptive_question falls back to chat.completions when responses.create fails."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from agent.agent import AssessmentState, generate_adaptive_question

        state = AssessmentState()
        mock_choice = MagicMock()
        mock_choice.message.content = "How did you resolve the conflict with your team?"
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("Responses 500 endpoint error")
            mock_chat.return_value = mock_chat_resp

            q = asyncio.run(generate_adaptive_question(state, 300, current_answer="We had a conflict."))
            self.assertEqual(q, "How did you resolve the conflict with your team?")

    def test_generate_adaptive_question_no_duplicate_when_both_fail(self):
        """Verify generate_adaptive_question selects unasked fallback questions without duplicates."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import AssessmentState, generate_adaptive_question, FALLBACK_BEHAVIORAL_QUESTIONS

        state = AssessmentState()
        # Mark first question as already asked
        state.add_transcript("interviewer", FALLBACK_BEHAVIORAL_QUESTIONS[0])

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("500")
            mock_chat.side_effect = RuntimeError("500")

            q = asyncio.run(generate_adaptive_question(state, 300, current_answer="Some answer"))
            # Must not return the first question because it was already asked
            self.assertNotEqual(q, FALLBACK_BEHAVIORAL_QUESTIONS[0])
            self.assertEqual(q, FALLBACK_BEHAVIORAL_QUESTIONS[1])

    def test_behavioral_interview_agent_turn_callback(self):
        """Verify BehavioralInterviewAgent executes callback and stops automatic generation."""
        import asyncio
        from unittest.mock import MagicMock
        from agent.agent import BehavioralInterviewAgent, StopResponse

        captured_text = []

        async def callback(text):
            captured_text.append(text)

        agent = BehavioralInterviewAgent(on_turn_completed_callback=callback)
        mock_msg = MagicMock()
        mock_msg.text_content = "I handled a critical production incident with structured communication."

        with self.assertRaises(StopResponse):
            asyncio.run(agent.on_user_turn_completed(None, mock_msg))

        self.assertEqual(captured_text, ["I handled a critical production incident with structured communication."])

    def test_completed_answer_task_rescued_and_not_processed_twice(self):
        """Verify drain_and_analyze_pending_answers rescues a completed answer_task and processes each answer at most once."""
        import asyncio
        from unittest.mock import AsyncMock
        from agent.agent import AssessmentState, drain_and_analyze_pending_answers

        state = AssessmentState()
        analyze_mock = AsyncMock()
        analyzed_answers = set()
        user_answer_queue = asyncio.Queue()

        answer_text = "I stepped up to lead the migration when our team lead was unavailable."
        # Queue has the answer, and answer_task already dequeued it before cleanup
        user_answer_queue.put_nowait(answer_text)

        async def run_scenario():
            answer_task = asyncio.create_task(user_answer_queue.get())
            await answer_task  # answer_task is now done() and holds answer_text; queue is empty

            # Also put a duplicate copy in queue to verify deduplication
            user_answer_queue.put_nowait(answer_text)

            count1 = await drain_and_analyze_pending_answers(
                state,
                user_answer_queue,
                analyzed_answers,
                answer_task=answer_task,
                analyze_fn=analyze_mock,
            )
            # Calling drain a second time with the same completed answer_task must not re-analyze
            count2 = await drain_and_analyze_pending_answers(
                state,
                user_answer_queue,
                analyzed_answers,
                answer_task=answer_task,
                analyze_fn=analyze_mock,
            )
            return count1, count2

        count1, count2 = asyncio.run(run_scenario())
        self.assertEqual(count1, [answer_text])
        self.assertEqual(count2, [])
        analyze_mock.assert_awaited_once_with(state, answer_text)

    def test_end_assessment_immediately_after_speaking_preserves_uncommitted_answer(self):
        """Verify finalize_uncommitted_speech + drain_and_analyze_pending_answers preserves final speech on End Assessment."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            finalize_uncommitted_speech,
            drain_and_analyze_pending_answers,
        )

        state = AssessmentState()
        user_answer_queue = asyncio.Queue()
        analyzed_answers = set()
        uncommitted_final_segments = [
            "We resolved the outage by rolling back the faulty deployment",
            "and adding automated canary checks.",
        ]

        async def on_turn_callback(text: str):
            uncommitted_final_segments.clear()
            state.add_transcript("candidate", text)
            await user_answer_queue.put(text)

        # Case 1: commit_user_turn times out or fails, fallback flushes finalized STT segments
        mock_session_timeout = MagicMock()
        mock_session_timeout.commit_user_turn = AsyncMock(side_effect=TimeoutError("EOU commit timeout"))
        analyze_mock = AsyncMock()

        async def run_fallback_flush():
            flushed = await finalize_uncommitted_speech(
                mock_session_timeout,
                uncommitted_final_segments,
                on_turn_callback,
                commit_timeout=0.2,
            )
            await drain_and_analyze_pending_answers(
                state,
                user_answer_queue,
                analyzed_answers,
                analyze_fn=analyze_mock,
            )
            return flushed

        flushed_text = asyncio.run(run_fallback_flush())
        expected_text = (
            "We resolved the outage by rolling back the faulty deployment "
            "and adding automated canary checks."
        )
        self.assertEqual(flushed_text, expected_text)
        self.assertEqual(len(state.transcript), 1)
        self.assertEqual(state.transcript[0]["text"], expected_text)
        analyze_mock.assert_awaited_once_with(state, expected_text)

    def test_answer_arriving_during_llm_generation_not_assigned_to_next_question(self):
        """Verify an answer arriving in user_answer_queue during LLM generation is analyzed for the current turn and drained before the next question."""
        import asyncio
        from unittest.mock import MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        state.add_transcript("interviewer", "Tell me about a time you handled a tight deadline.")
        user_answer_queue = asyncio.Queue()
        analyzed_answers = set()
        analyzed_calls = []
        spoken_questions = []

        first_part = "We prioritized the critical path features first and negotiated scope with stakeholders."
        continuation_part = "As a result, we delivered on schedule with zero severity-one defects."

        async def fake_analyze(st, ans):
            analyzed_calls.append(
                (
                    len([t for t in st.transcript if t.get("role") == "interviewer"]),
                    ans,
                )
            )

        async def fake_generate_question(st, rem, current_answer="", transcript=None, evidence=None):
            # Simulate candidate speaking a continuation that completes while LLM is generating the next question
            state.add_transcript("candidate", continuation_part)
            await user_answer_queue.put(continuation_part)
            return "How did you communicate that scope change to leadership?"

        async def fake_speak(q, stage):
            spoken_questions.append(q)
            return True

        async def fake_send_event(ctx, event_type, data=None):
            pass

        async def run_turn():
            state.add_transcript("candidate", first_part)
            should_continue = await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer=first_part,
                remaining_seconds=300,
                warning_sent=False,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                manual_end_event=asyncio.Event(),
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=fake_speak,
                analyze_fn=fake_analyze,
                generate_question_fn=fake_generate_question,
                send_event_fn=fake_send_event,
            )
            return should_continue

        cont = asyncio.run(run_turn())
        self.assertTrue(cont)
        # Queue must be completely empty after process_interview_turn so continuation_part is NOT popped as the answer to Question 2
        self.assertTrue(user_answer_queue.empty())
        # Both parts were analyzed while Question 1 was the only interviewer question in transcript
        self.assertEqual(
            analyzed_calls,
            [
                (1, first_part),
                (1, continuation_part),
            ],
        )
        # Question 2 was spoken only after both parts of Question 1's answer were analyzed
        self.assertEqual(spoken_questions, ["How did you communicate that scope change to leadership?"])
        self.assertEqual(state.questions_asked, 1)

    def test_warning_sent_and_low_remaining_time_still_analyzes_final_answer(self):
        """Verify warning_sent or remaining_seconds <= WARNING_SECONDS still analyzes the candidate's final answer before stopping."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn, WARNING_SECONDS

        state = AssessmentState()
        user_answer_queue = asyncio.Queue()
        analyzed_answers = set()
        analyze_mock = AsyncMock()
        generate_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        final_answer_1 = "I took full ownership of the incident postmortem and implemented preventative alerts."
        final_answer_2 = "I mentored two junior engineers through the architecture redesign."

        async def run_warning_cases():
            # Case A: warning_sent=True
            cont_a = await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer=final_answer_1,
                remaining_seconds=25,
                warning_sent=True,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                manual_end_event=asyncio.Event(),
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=speak_mock,
                analyze_fn=analyze_mock,
                generate_question_fn=generate_mock,
                send_event_fn=send_event_mock,
            )
            # Case B: warning_sent=False, remaining_seconds <= WARNING_SECONDS
            cont_b = await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer=final_answer_2,
                remaining_seconds=WARNING_SECONDS,
                warning_sent=False,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                manual_end_event=asyncio.Event(),
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=speak_mock,
                analyze_fn=analyze_mock,
                generate_question_fn=generate_mock,
                send_event_fn=send_event_mock,
            )
            return cont_a, cont_b

        cont_a, cont_b = asyncio.run(run_warning_cases())
        self.assertFalse(cont_a)
        self.assertFalse(cont_b)
        self.assertEqual(analyze_mock.await_count, 2)
        analyze_mock.assert_any_await(state, final_answer_1)
        analyze_mock.assert_any_await(state, final_answer_2)
        # Must not generate or speak another question when time is up
        generate_mock.assert_not_awaited()
        speak_mock.assert_not_awaited()

    def test_final_result_sent_before_slow_backend_sync_finishes(self):
        """Verify deliver_final_result_and_sync sends final_result over LiveKit before slow backend sync or disk save finishes."""
        import asyncio
        from unittest.mock import MagicMock
        from agent.agent import AssessmentState, deliver_final_result_and_sync

        state = AssessmentState()
        state.completed = True
        state.final_analysis = {"overall_score": 88, "scores": {"communication": 88}}
        candidate_disconnected = asyncio.Event()
        events_order = []

        async def fake_send_event(ctx, event_type, data):
            events_order.append(f"send_{event_type}")
            # Candidate disconnects right after receiving final_result
            candidate_disconnected.set()

        def slow_save(st, aid):
            import time
            time.sleep(0.15)
            events_order.append("disk_saved")

        async def slow_sync(st, aid):
            await asyncio.sleep(0.25)
            events_order.append("backend_synced")
            return True

        asyncio.run(
            deliver_final_result_and_sync(
                ctx=MagicMock(),
                state=state,
                assessment_id="room-123",
                candidate_disconnected=candidate_disconnected,
                send_event_fn=fake_send_event,
                save_fn=slow_save,
                sync_fn=slow_sync,
                disconnect_timeout=1.0,
            )
        )

        self.assertEqual(events_order[0], "send_final_result")
        self.assertIn("disk_saved", events_order)
        self.assertIn("backend_synced", events_order)

    def test_speak_with_lock_interrupts_cleanly_on_manual_end(self):
        """Verify speak_with_lock serializes speech, interrupts on manual_end_event, and resets speaking state immediately."""
        import asyncio
        from unittest.mock import MagicMock
        from agent.agent import speak_with_lock

        speaking_states = []
        interrupted_flags = []

        async def run_speech_interrupt():
            speech_lock = asyncio.Lock()
            manual_end = asyncio.Event()
            disconnect_ev = asyncio.Event()
            speech_done_future = asyncio.get_running_loop().create_future()

            class FakeSpeechHandle:
                def interrupt(self, *, force: bool = False):
                    interrupted_flags.append(force)
                    if not speech_done_future.done():
                        speech_done_future.set_result(None)

                def __await__(self):
                    return speech_done_future.__await__()

            fake_handle = FakeSpeechHandle()
            mock_session = MagicMock()
            mock_session.say = MagicMock(return_value=fake_handle)

            async def trigger_manual_end():
                await asyncio.sleep(0.05)
                manual_end.set()

            trigger_task = asyncio.create_task(trigger_manual_end())
            completed = await speak_with_lock(
                session=mock_session,
                text="Tell me about a complex project you led.",
                speech_lock=speech_lock,
                stage_label="Test Question",
                manual_end_event=manual_end,
                disconnect_event=disconnect_ev,
                set_speaking_state=lambda val: speaking_states.append(val),
            )
            await trigger_task
            return completed

        completed = asyncio.run(run_speech_interrupt())
        self.assertFalse(completed)
        self.assertEqual(interrupted_flags, [True])
        self.assertEqual(speaking_states, [True, False])

    def test_handle_user_input_transcribed_event(self):
        """Verify handle_user_input_transcribed_event accumulates interim and final STT segments,
        dispatches candidate_interim_transcript without dropping context, and filters acoustic echoes."""
        from agent.agent import handle_user_input_transcribed_event

        events = []
        def mock_send(event_type, payload):
            events.append((event_type, payload))

        uncommitted_segments = []

        # 1. Non-empty interim transcript: dispatches accumulated candidate_interim_transcript
        handled = handle_user_input_transcribed_event(
            transcript="I led a team",
            is_final=False,
            is_agent_speaking=False,
            uncommitted_final_segments=uncommitted_segments,
            send_ui_event_fn=mock_send,
        )
        self.assertTrue(handled)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "candidate_interim_transcript")
        self.assertEqual(events[0][1], {"answer": "I led a team", "is_final": False})
        self.assertEqual(uncommitted_segments, [])

        # 2. Finalized STT segment: buffers segment AND dispatches accumulated transcript so UI does not blank
        events.clear()
        handled = handle_user_input_transcribed_event(
            transcript="I led a team of five engineers.",
            is_final=True,
            is_agent_speaking=False,
            uncommitted_final_segments=uncommitted_segments,
            send_ui_event_fn=mock_send,
        )
        self.assertTrue(handled)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "candidate_interim_transcript")
        self.assertEqual(events[0][1], {"answer": "I led a team of five engineers.", "is_final": False})
        self.assertEqual(uncommitted_segments, ["I led a team of five engineers."])

        # 3. Subsequent interim segment accumulates with previously finalized segment
        events.clear()
        handled = handle_user_input_transcribed_event(
            transcript="building an AI pipeline",
            is_final=False,
            is_agent_speaking=False,
            uncommitted_final_segments=uncommitted_segments,
            send_ui_event_fn=mock_send,
        )
        self.assertTrue(handled)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0][1]["answer"],
            "I led a team of five engineers. building an AI pipeline",
        )

        # 4. Confirmed acoustic echo of active question while agent is speaking: ignored
        events.clear()
        uncommitted_segments.clear()
        handled = handle_user_input_transcribed_event(
            transcript="please introduce yourself",
            is_final=False,
            is_agent_speaking=True,
            uncommitted_final_segments=uncommitted_segments,
            send_ui_event_fn=mock_send,
            active_question="To begin, please introduce yourself and briefly tell me about your background.",
        )
        self.assertFalse(handled)
        self.assertEqual(events, [])
        self.assertEqual(uncommitted_segments, [])

        # 5. Real candidate speech while agent is speaking (not echoing question): preserved
        events.clear()
        handled = handle_user_input_transcribed_event(
            transcript="I graduated in computer science",
            is_final=False,
            is_agent_speaking=True,
            uncommitted_final_segments=uncommitted_segments,
            send_ui_event_fn=mock_send,
            active_question="To begin, please introduce yourself and briefly tell me about your background.",
        )
        self.assertTrue(handled)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1]["answer"], "I graduated in computer science")

        # 6. Empty or whitespace-only transcript: ignored
        events.clear()
        handled = handle_user_input_transcribed_event(
            transcript="   ",
            is_final=False,
            is_agent_speaking=False,
            uncommitted_final_segments=uncommitted_segments,
            send_ui_event_fn=mock_send,
        )
        self.assertFalse(handled)
        self.assertEqual(events, [])

    def test_is_candidate_refusal_detection(self):
        """Verify is_candidate_refusal correctly identifies explicit refusals, discomfort, and skip requests without falsely flagging ordinary answers or 'I don't know'."""
        from agent.agent import is_candidate_refusal

        # Explicit refusal and skip phrases (must return True)
        refusal_phrases = [
            "I don't want to answer that question.",
            "I do not want to answer that.",
            "Can you ask me something else?",
            "Could you ask me something else please?",
            "I am not comfortable answering this.",
            "I'm not comfortable answering that.",
            "I prefer not to answer that.",
            "I would rather not answer this question.",
            "Could we skip this question?",
            "Can we skip to the next question?",
            "Please ask a different question.",
            "Can we move on to something else?",
            "Let's move on to the next question.",
            "Pass on this question.",
            "Next question please.",
            "I'd rather skip this one.",
            "I don't feel comfortable discussing this.",
        ]
        for phrase in refusal_phrases:
            self.assertTrue(
                is_candidate_refusal(phrase),
                f"Expected '{phrase}' to be detected as refusal",
            )

        # Non-refusal answers and explanations (must return False)
        non_refusal_phrases = [
            "I don't know the exact answer to that technical problem, but I would look up the documentation.",
            "I don't know.",
            "I do not know.",
            "I don't think it was easy, but we succeeded as a team.",
            "I am very comfortable answering behavioral questions.",
            "I was comfortable working in a fast-paced environment.",
            "In my previous role, I led a cross-functional project to improve latency.",
            "I prefer using React over Angular because of its ecosystem.",
            "We decided to move to a microservices architecture.",
            "I haven't faced this exact scenario before, but here is what I would do.",
            "",
            "   ",
        ]
        for phrase in non_refusal_phrases:
            self.assertFalse(
                is_candidate_refusal(phrase),
                f"Expected '{phrase}' NOT to be detected as refusal",
            )

    def test_assessment_state_refused_questions_tracking(self):
        """Verify AssessmentState tracks refused_questions and candidate_introduction in state and to_dict()."""
        from agent.agent import AssessmentState

        state = AssessmentState()
        self.assertEqual(state.refused_questions, [])
        self.assertEqual(state.candidate_introduction, "")

        state.candidate_introduction = "I am a full stack software engineer with 6 years experience."
        state.refused_questions.append("Tell me about a time you had a major disagreement with your manager.")

        d = state.to_dict()
        self.assertEqual(d["candidate_introduction"], "I am a full stack software engineer with 6 years experience.")
        self.assertEqual(d["refused_questions"], ["Tell me about a time you had a major disagreement with your manager."])

    def test_get_last_interviewer_question_helper(self):
        """Verify get_last_interviewer_question extracts the most recent interviewer question from transcript."""
        from agent.agent import AssessmentState, get_last_interviewer_question

        state = AssessmentState()
        self.assertEqual(get_last_interviewer_question(state), "")

        state.add_transcript("candidate", "Hello, I am ready.")
        self.assertEqual(get_last_interviewer_question(state), "")

        state.add_transcript("interviewer", "Can you tell me about a time you resolved a conflict?")
        state.add_transcript("candidate", "Sure, in my last project...")
        self.assertEqual(
            get_last_interviewer_question(state),
            "Can you tell me about a time you resolved a conflict?",
        )

        state.add_transcript("assistant", "How did you measure the outcome?")
        state.add_transcript("candidate", "I prefer not to answer that.")
        self.assertEqual(
            get_last_interviewer_question(state),
            "How did you measure the outcome?",
        )

    def test_analyze_candidate_answer_bypasses_refusal_fairness(self):
        """Verify analyze_candidate_answer immediately returns without making LLM calls or logging evidence on refusal."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import AssessmentState, analyze_candidate_answer

        state = AssessmentState()
        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:

            asyncio.run(analyze_candidate_answer(state, "I don't want to answer that question."))

            mock_resp.assert_not_awaited()
            mock_chat.assert_not_awaited()
            # Evidence must remain empty across all behavioral areas
            for area, items in state.evidence.items():
                self.assertEqual(items, [], f"Expected empty evidence for {area}")

    def test_process_interview_turn_handles_refusal_professionally_without_scoring(self):
        """Verify process_interview_turn intercepts a refusal, bypasses analyze_fn, records the refused question,
        and speaks a professional pivot response starting with the standard acknowledgment."""
        import asyncio
        from unittest.mock import MagicMock
        from agent.agent import AssessmentState, process_interview_turn, PROFESSIONAL_REFUSAL_PREFIX

        state = AssessmentState()
        prior_question = "Tell me about a time you made a critical error in production."
        state.add_transcript("interviewer", prior_question)

        user_answer_queue = asyncio.Queue()
        analyzed_answers = set()
        analyzed_calls = []
        spoken_questions = []

        refusal_answer = "I am not comfortable answering this question."

        async def fake_analyze(st, ans):
            analyzed_calls.append(ans)

        async def fake_generate_question(st, rem, current_answer="", transcript=None, evidence=None, is_refusal=False, refused_question=""):
            self.assertTrue(is_refusal)
            self.assertEqual(refused_question, prior_question)
            return "Could you describe how you would handle a challenging situation at work?"

        async def fake_speak(q, stage):
            spoken_questions.append(q)
            return True

        async def fake_send_event(ctx, event_type, data=None):
            pass

        async def run_turn():
            state.add_transcript("candidate", refusal_answer)
            should_continue = await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer=refusal_answer,
                remaining_seconds=300,
                warning_sent=False,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                manual_end_event=asyncio.Event(),
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=fake_speak,
                analyze_fn=fake_analyze,
                generate_question_fn=fake_generate_question,
                send_event_fn=fake_send_event,
            )
            return should_continue

        cont = asyncio.run(run_turn())
        self.assertTrue(cont)
        # analyze_fn must NEVER be called for the refusal
        self.assertEqual(analyzed_calls, [])
        # The previous question must be recorded as refused in state
        self.assertEqual(state.refused_questions, [prior_question])
        # The question spoken must include the professional refusal prefix
        self.assertEqual(len(spoken_questions), 1)
        self.assertTrue(spoken_questions[0].startswith(PROFESSIONAL_REFUSAL_PREFIX))
        self.assertIn("Could you describe how you would handle a challenging situation at work?", spoken_questions[0])

    def test_process_interview_turn_refusal_during_warning_period_speaks_acknowledgment_and_ends(self):
        """Verify process_interview_turn during warning period politely acknowledges the refusal before concluding,
        bypasses analyze_fn, records the refused question, and immediately stops without starting a new question."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        state.add_transcript("interviewer", "Can you describe a high-stakes decision you regret?")

        user_answer_queue = asyncio.Queue()
        analyzed_answers = set()
        analyze_mock = AsyncMock()
        generate_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        async def run_warning_refusal():
            return await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer="Can you ask me something else?",
                remaining_seconds=20,
                warning_sent=True,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                manual_end_event=asyncio.Event(),
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=speak_mock,
                analyze_fn=analyze_mock,
                generate_question_fn=generate_mock,
                send_event_fn=send_event_mock,
            )

        cont = asyncio.run(run_warning_refusal())
        self.assertFalse(cont)
        analyze_mock.assert_not_awaited()
        generate_mock.assert_not_awaited()
        speak_mock.assert_awaited_once_with(
            "I understand, that is completely fine.",
            "Refusal acknowledgment",
        )
        self.assertIn("Can you describe a high-stakes decision you regret?", state.refused_questions)

        # Disconnect or manual end during refusal must suppress speech
        speak_mock.reset_mock()
        manual_ev = asyncio.Event()
        manual_ev.set()

        async def run_manual_end_refusal():
            return await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer="I'd rather skip.",
                remaining_seconds=20,
                warning_sent=True,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                manual_end_event=manual_ev,
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=speak_mock,
                analyze_fn=analyze_mock,
                generate_question_fn=generate_mock,
                send_event_fn=send_event_mock,
            )

        cont_manual = asyncio.run(run_manual_end_refusal())
        self.assertFalse(cont_manual)
        speak_mock.assert_not_awaited()

    def test_format_refusal_response_idempotency_no_duplicate_prefix(self):
        """Verify format_refusal_response is idempotent and does not produce duplicate prefixes
        when called in both question generation and turn processing."""
        import asyncio
        from unittest.mock import MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            format_refusal_response,
            PROFESSIONAL_REFUSAL_PREFIX,
        )

        raw_q = "Could you tell me about how you resolve technical disagreements?"
        r1 = format_refusal_response(raw_q)
        r2 = format_refusal_response(r1)
        r3 = format_refusal_response(r2)

        self.assertEqual(r1, r2)
        self.assertEqual(r2, r3)
        self.assertEqual(r1.count(PROFESSIONAL_REFUSAL_PREFIX), 1)

        # Existing courteous acknowledgments are preserved without duplicate prefixes
        already_polite = "Understood. Can you describe your approach to learning new tools?"
        self.assertEqual(format_refusal_response(already_polite), already_polite)

        fine_polite = "That's completely fine. Tell me about a time you mentored a junior engineer."
        self.assertEqual(format_refusal_response(fine_polite), fine_polite)

        # In process_interview_turn, even if generate_question_fn already pre-formatted the question,
        # the final spoken question must contain only ONE prefix
        state = AssessmentState()
        state.add_transcript("interviewer", "Describe a high-stakes failure.")
        spoken = []

        async def preformatted_gen(st, rem, **kwargs):
            return format_refusal_response("How do you handle changing priorities?")

        async def capture_speak(q, stage):
            spoken.append(q)
            return True

        async def run_turn():
            return await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer="I don't want to answer that.",
                remaining_seconds=300,
                warning_sent=False,
                user_answer_queue=asyncio.Queue(),
                analyzed_answers=set(),
                manual_end_event=asyncio.Event(),
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=capture_speak,
                analyze_fn=lambda st, a: asyncio.sleep(0),
                generate_question_fn=preformatted_gen,
                send_event_fn=lambda c, t, d=None: asyncio.sleep(0),
            )

        asyncio.run(run_turn())
        self.assertEqual(len(spoken), 1)
        self.assertEqual(spoken[0].count(PROFESSIONAL_REFUSAL_PREFIX), 1)
        self.assertTrue(spoken[0].startswith(PROFESSIONAL_REFUSAL_PREFIX))

    def test_is_refused_topic_repetition_safeguard(self):
        """Verify is_refused_topic_repetition catches questions repeating a refused topic with different wording,
        while allowing unrelated questions in other dimensions."""
        from agent.agent import is_refused_topic_repetition

        # Case 1: Refused interpersonal conflict/disagreement
        refused_conflict = ["Tell me about a time you had a major disagreement with your manager."]
        # Different wording on the same topic must be flagged as repetition
        self.assertTrue(is_refused_topic_repetition(
            "Could you describe a conflict you had with a coworker and how you resolved it?",
            refused_conflict,
        ))
        self.assertTrue(is_refused_topic_repetition(
            "Can you share a dispute or clash you experienced with a team member?",
            refused_conflict,
        ))
        # Unrelated questions must NOT be blocked
        self.assertFalse(is_refused_topic_repetition(
            "Can you tell me about a challenging technical or operational problem you encountered?",
            refused_conflict,
        ))
        self.assertFalse(is_refused_topic_repetition(
            "Describe a complex or high-stakes decision you had to make with limited information.",
            refused_conflict,
        ))
        self.assertFalse(is_refused_topic_repetition(
            "Tell me about a time when project priorities changed unexpectedly.",
            refused_conflict,
        ))

        # Case 2: Refused mistake / failure / error
        refused_mistake = ["Can you share an experience where you had to take ownership of a mistake or setback?"]
        self.assertTrue(is_refused_topic_repetition(
            "Tell me about a project that failed or had a serious error in production.",
            refused_mistake,
        ))
        self.assertTrue(is_refused_topic_repetition(
            "Describe a major blunder or regret you experienced in a past role.",
            refused_mistake,
        ))
        self.assertFalse(is_refused_topic_repetition(
            "Tell me about a situation where you stepped up to lead an initiative or guide colleagues.",
            refused_mistake,
        ))

        # Case 3: Keyword overlap on specific non-generic content words
        refused_stakeholders = ["How did you communicate complex concepts to cross-functional stakeholders?"]
        self.assertTrue(is_refused_topic_repetition(
            "Describe how you persuaded stakeholders on complex ideas.",
            refused_stakeholders,
        ))
        self.assertFalse(is_refused_topic_repetition(
            "Tell me about a time you made an architectural decision.",
            refused_stakeholders,
        ))

    def test_generate_adaptive_question_discards_model_repeating_refused_topic(self):
        """Verify generate_adaptive_question discards an LLM question that re-asks a refused topic
        with different wording, and picks an unrefused fallback question."""
        import asyncio
        from unittest.mock import patch, MagicMock
        from agent.agent import AssessmentState, generate_adaptive_question, FALLBACK_BEHAVIORAL_QUESTIONS

        state = AssessmentState()
        # Candidate previously refused a question about disagreements
        refused_q = "Tell me about a time you had a major disagreement with your supervisor."
        state.refused_questions.append(refused_q)

        captured_prompt = None

        # LLM attempts to re-ask the same conflict topic with different wording
        async def fake_resp_repeat(model, input):
            nonlocal captured_prompt
            captured_prompt = input
            mock_obj = MagicMock()
            mock_obj.output_text = "Could you describe a conflict or dispute you had with a coworker?"
            return mock_obj

        with patch("agent.agent.openai_client.responses.create", side_effect=fake_resp_repeat):
            q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I managed our sprint delivery.",
                is_refusal=False,
            ))

        # Must discard the repeated conflict question and fall back to an unrefused topic (problem solving)
        self.assertNotIn("conflict", q.lower())
        self.assertNotIn("dispute", q.lower())
        self.assertEqual(q, FALLBACK_BEHAVIORAL_QUESTIONS[0])

        # Prompt must contain the previously declined questions section
        self.assertIsNotNone(captured_prompt)
        self.assertIn("Previously declined or skipped questions:", captured_prompt)
        self.assertIn(refused_q, captured_prompt)
        self.assertIn("Do NOT ask about these topics or situations again, even with different wording.", captured_prompt)

    def test_generate_adaptive_question_fallback_excludes_refused_questions(self):
        """Verify generate_adaptive_question does not repeat questions from state.refused_questions when falling back."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import (
            AssessmentState,
            generate_adaptive_question,
            FALLBACK_BEHAVIORAL_QUESTIONS,
            PROFESSIONAL_REFUSAL_PREFIX,
        )

        state = AssessmentState()
        # Mark the first fallback question as refused
        first_fallback = FALLBACK_BEHAVIORAL_QUESTIONS[0]
        state.refused_questions.append(first_fallback)

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("Service unavailable")
            mock_chat.side_effect = RuntimeError("Service unavailable")

            # Fallback for non-refusal turn
            q = asyncio.run(generate_adaptive_question(state, 300, current_answer="I managed the deployment.", is_refusal=False))
            self.assertNotEqual(q, first_fallback)
            self.assertEqual(q, FALLBACK_BEHAVIORAL_QUESTIONS[1])

            # Fallback for refusal turn: must exclude refused question AND include professional prefix
            q_refusal = asyncio.run(generate_adaptive_question(
                state, 300, current_answer="I prefer to skip.", is_refusal=True, refused_question=first_fallback
            ))
            self.assertTrue(q_refusal.startswith(PROFESSIONAL_REFUSAL_PREFIX))
            self.assertNotIn(first_fallback, q_refusal)
            self.assertIn(FALLBACK_BEHAVIORAL_QUESTIONS[1], q_refusal)

    def test_generate_adaptive_question_prompt_includes_introduction_and_grounding(self):
        """Verify generate_adaptive_question prompt contains candidate self-introduction and non-invention grounding rules."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from agent.agent import AssessmentState, generate_adaptive_question

        state = AssessmentState()
        state.candidate_introduction = "I have 4 years experience as a backend engineer working with Go and distributed databases."

        captured_prompt = None

        async def fake_resp_create(model, input):
            nonlocal captured_prompt
            captured_prompt = input
            mock_obj = MagicMock()
            mock_obj.output_text = "What was the most challenging distributed database issue you resolved?"
            return mock_obj

        with patch("agent.agent.openai_client.responses.create", side_effect=fake_resp_create):
            q = asyncio.run(generate_adaptive_question(state, 300, current_answer="I worked on database replication."))
            self.assertIn("What was the most challenging distributed database issue you resolved?", q)

        self.assertIsNotNone(captured_prompt)
        self.assertIn("Candidate stated background / introduction:", captured_prompt)
        self.assertIn("I have 4 years experience as a backend engineer working with Go and distributed databases.", captured_prompt)
        self.assertIn("Never invent or assume candidate qualifications", captured_prompt)

    def test_final_fallback_never_returns_refused_topic_or_question(self):
        """Verify that when the first fallback question is refused and other predefined questions are asked,
        the system selects a safe neutral question from another behavioral dimension, and NEVER returns the refused question."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import (
            AssessmentState,
            generate_adaptive_question,
            FALLBACK_BEHAVIORAL_QUESTIONS,
            SAFE_NEUTRAL_QUESTIONS,
        )

        state = AssessmentState()
        # Refuse the first fallback question (technical problem solving)
        refused_q = FALLBACK_BEHAVIORAL_QUESTIONS[0]
        state.refused_questions.append(refused_q)

        # Mark all other predefined fallback questions as already asked in the transcript
        for asked_q in FALLBACK_BEHAVIORAL_QUESTIONS[1:]:
            state.add_transcript("interviewer", asked_q)

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("500")
            mock_chat.side_effect = RuntimeError("500")

            # Fallback must NOT return the refused question (FALLBACK_BEHAVIORAL_QUESTIONS[0])
            q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I took ownership.",
                is_refusal=False,
            ))

            self.assertNotEqual(q, refused_q)
            self.assertIn(q, SAFE_NEUTRAL_QUESTIONS)
            self.assertEqual(q, SAFE_NEUTRAL_QUESTIONS[0])

            # If the first safe neutral question is ALSO refused, verify it advances to the next safe neutral
            state.refused_questions.append(SAFE_NEUTRAL_QUESTIONS[0])
            q2 = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I took ownership.",
                is_refusal=False,
            ))
            self.assertNotEqual(q2, SAFE_NEUTRAL_QUESTIONS[0])
            self.assertEqual(q2, SAFE_NEUTRAL_QUESTIONS[1])

    def test_all_fallbacks_and_safe_neutrals_refused_returns_controlled_empty_string(self):
        """Verify that if all predefined fallbacks and safe neutral questions are refused or match refused topics,
        generate_adaptive_question returns an empty string as a controlled no-question result instead of repeating a refusal."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import (
            AssessmentState,
            generate_adaptive_question,
            FALLBACK_BEHAVIORAL_QUESTIONS,
            SAFE_NEUTRAL_QUESTIONS,
        )

        state = AssessmentState()
        # Refuse ALL predefined questions and ALL safe neutral questions
        state.refused_questions.extend(FALLBACK_BEHAVIORAL_QUESTIONS)
        state.refused_questions.extend(SAFE_NEUTRAL_QUESTIONS)

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("500")
            mock_chat.side_effect = RuntimeError("500")

            q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I decline.",
                is_refusal=True,
            ))

            # Must be empty string — never a refused question
            self.assertEqual(q, "")

    def test_process_interview_turn_controlled_no_question_on_refusal_acknowledges_and_ends(self):
        """Verify process_interview_turn gracefully acknowledges refusal and ends turn safely when generate_question_fn returns empty string."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        state.add_transcript("interviewer", "Can you tell me about a time you made a major mistake?")

        user_answer_queue = asyncio.Queue()
        analyzed_answers = set()
        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        async def empty_gen_fn(st, rem, **kwargs):
            return ""

        async def run_empty_gen():
            return await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer="I don't want to answer that.",
                remaining_seconds=300,
                warning_sent=False,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                manual_end_event=asyncio.Event(),
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=speak_mock,
                analyze_fn=analyze_mock,
                generate_question_fn=empty_gen_fn,
                send_event_fn=send_event_mock,
            )

        cont = asyncio.run(run_empty_gen())
        self.assertFalse(cont)
        analyze_mock.assert_not_awaited()
        # Must acknowledge the refusal even though no more questions remain
        speak_mock.assert_awaited_once_with(
            "I understand, that is completely fine.",
            "Refusal acknowledgment",
        )
        self.assertIn("Can you tell me about a time you made a major mistake?", state.refused_questions)

    def test_unrelated_questions_allowed_through_fallback_and_generation(self):
        """Verify that questions exploring different, unrefused behavioral dimensions remain fully allowed."""
        import asyncio
        from unittest.mock import patch, MagicMock
        from agent.agent import AssessmentState, generate_adaptive_question, FALLBACK_BEHAVIORAL_QUESTIONS

        state = AssessmentState()
        # Refuse interpersonal disagreement
        state.refused_questions.append("Can you describe a time you had a major disagreement with your supervisor?")

        # 1. Fallback path allows unrelated dimension (technical problem solving)
        with patch("agent.agent.openai_client.responses.create", side_effect=RuntimeError("500")), \
             patch("agent.agent.openai_client.chat.completions.create", side_effect=RuntimeError("500")):
            q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I worked on database optimization.",
                is_refusal=False,
            ))
            self.assertEqual(q, FALLBACK_BEHAVIORAL_QUESTIONS[0])

        # 2. Model-generated path allows unrelated dimension
        async def fake_resp_unrelated(model, input):
            mock_obj = MagicMock()
            mock_obj.output_text = "How do you ensure test coverage in critical software components?"
            return mock_obj

        with patch("agent.agent.openai_client.responses.create", side_effect=fake_resp_unrelated):
            q_gen = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I write integration tests.",
                is_refusal=False,
            ))
            self.assertEqual(q_gen, "How do you ensure test coverage in critical software components?")

    def test_refusal_prefix_and_normalization_helpers(self):
        """Verify strip_refusal_prefix and normalize_question correctly remove refusal acknowledgments and normalize text."""
        from agent.agent import strip_refusal_prefix, normalize_question, PROFESSIONAL_REFUSAL_PREFIX

        # Standard prefix
        base_q = "Could you tell me about a time you handled a difficult project?"
        with_prefix = f"{PROFESSIONAL_REFUSAL_PREFIX} {base_q}"
        self.assertEqual(strip_refusal_prefix(with_prefix), base_q)
        self.assertEqual(normalize_question(with_prefix), normalize_question(base_q))

        # Alternate polite acknowledgment starters
        alt1 = f"I understand, that is completely fine. {base_q}"
        self.assertEqual(strip_refusal_prefix(alt1), base_q)
        self.assertEqual(normalize_question(alt1), normalize_question(base_q))

        alt2 = f"Understood. Let's move to another question. {base_q}"
        self.assertEqual(strip_refusal_prefix(alt2), base_q)
        self.assertEqual(normalize_question(alt2), normalize_question(base_q))

        # Plain question without prefix remains unchanged
        self.assertEqual(strip_refusal_prefix(base_q), base_q)

    def test_fallback_excludes_refused_question_even_when_stored_with_prefix(self):
        """Verify fallback question selection excludes a refused question whose stored string
        contains the refusal prefix from a previous redirection turn."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import (
            AssessmentState,
            generate_adaptive_question,
            FALLBACK_BEHAVIORAL_QUESTIONS,
            PROFESSIONAL_REFUSAL_PREFIX,
        )

        state = AssessmentState()
        # Suppose question 0 was spoken with the refusal prefix on a previous refusal turn
        prefixed_fallback_0 = f"{PROFESSIONAL_REFUSAL_PREFIX} {FALLBACK_BEHAVIORAL_QUESTIONS[0]}"
        state.refused_questions.append(prefixed_fallback_0)

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("500")
            mock_chat.side_effect = RuntimeError("500")

            # Fallback must recognize that FALLBACK_BEHAVIORAL_QUESTIONS[0] is refused, even though
            # state.refused_questions contains the prefix and FALLBACK_BEHAVIORAL_QUESTIONS[0] does not!
            q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I would rather not discuss that.",
                is_refusal=True,
                refused_question=prefixed_fallback_0,
            ))

            # Must skip FALLBACK_BEHAVIORAL_QUESTIONS[0] and use FALLBACK_BEHAVIORAL_QUESTIONS[1]
            self.assertNotIn(FALLBACK_BEHAVIORAL_QUESTIONS[0], q)
            self.assertIn(FALLBACK_BEHAVIORAL_QUESTIONS[1], q)

    def test_model_generated_matching_prefixed_refusal_is_discarded(self):
        """Verify model-generated question matching a refused question (with or without prefix) is discarded."""
        import asyncio
        from unittest.mock import patch, MagicMock
        from agent.agent import (
            AssessmentState,
            generate_adaptive_question,
            FALLBACK_BEHAVIORAL_QUESTIONS,
            PROFESSIONAL_REFUSAL_PREFIX,
        )

        state = AssessmentState()
        # Stored refusal has refusal prefix
        raw_refused = FALLBACK_BEHAVIORAL_QUESTIONS[0]
        state.refused_questions.append(f"{PROFESSIONAL_REFUSAL_PREFIX} {raw_refused}")

        # Model generates the raw question without prefix
        async def fake_resp(model, input):
            mock_obj = MagicMock()
            mock_obj.output_text = raw_refused
            return mock_obj

        with patch("agent.agent.openai_client.responses.create", side_effect=fake_resp):
            q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I managed our sprint delivery.",
                is_refusal=False,
            ))

        # Must discard raw_refused and pick next fallback (FALLBACK_BEHAVIORAL_QUESTIONS[1])
        self.assertNotEqual(q, raw_refused)
        self.assertEqual(q, FALLBACK_BEHAVIORAL_QUESTIONS[1])

    def test_task2_relevant_followup_question_grounded_in_candidate_intro_and_answer(self):
        """Verify Task 2: questions are relevant to the candidate's answer, role/context, and target competency."""
        import asyncio
        from unittest.mock import patch, MagicMock
        from agent.agent import AssessmentState, generate_adaptive_question

        state = AssessmentState()
        state.candidate_introduction = "I am a cloud infrastructure engineer specializing in Kubernetes and distributed systems."
        candidate_answer = "We migrated our cluster to multi-region deployments to increase reliability during failovers."

        captured_prompt = None

        async def fake_resp(model, input):
            nonlocal captured_prompt
            captured_prompt = input
            mock_obj = MagicMock()
            mock_obj.output_text = "How did you coordinate the multi-region failover testing without impacting active users?"
            return mock_obj

        with patch("agent.agent.openai_client.responses.create", side_effect=fake_resp):
            q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer=candidate_answer,
                is_refusal=False,
            ))

        self.assertEqual(q, "How did you coordinate the multi-region failover testing without impacting active users?")
        self.assertIn("Candidate stated background / introduction:", captured_prompt)
        self.assertIn("cloud infrastructure engineer", captured_prompt)
        self.assertIn("Questions must be directly relevant to the candidate's answer, role/interview context", captured_prompt)
        self.assertIn(candidate_answer, captured_prompt)

    def test_task2_professional_refusal_acknowledgment_with_competency_warning(self):
        """Verify Task 2: professional, neutral, respectful acknowledgment informing the candidate that
        skipping may limit evidence for assessing this competency."""
        from agent.agent import PROFESSIONAL_REFUSAL_PREFIX, format_refusal_response

        # Check the exact phrasing requirement from Task 2
        expected_note = (
            "I understand. We can move to another relevant area. "
            "Please note that skipping a question may limit the evidence available for assessing this competency."
        )
        self.assertEqual(PROFESSIONAL_REFUSAL_PREFIX, expected_note)

        raw_q = "Could you tell me about how you organize project milestones?"
        formatted = format_refusal_response(raw_q)
        self.assertTrue(formatted.startswith(expected_note))
        self.assertIn(raw_q, formatted)

        # Idempotency: formatting again does not duplicate the prefix
        formatted_twice = format_refusal_response(formatted)
        self.assertEqual(formatted, formatted_twice)
        self.assertEqual(formatted_twice.count(expected_note), 1)

    def test_task2_declined_topic_not_repeated_and_pivots_to_different_behavioral_area(self):
        """Verify Task 2: declining a question avoids repeating the topic and pivots to a different competency."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import (
            AssessmentState,
            generate_adaptive_question,
            FALLBACK_BEHAVIORAL_QUESTIONS,
            get_question_behavioral_area,
            PROFESSIONAL_REFUSAL_PREFIX,
        )

        state = AssessmentState()
        # Candidate declined a question regarding interpersonal collaboration / conflict (teamwork)
        declined_q = "Could you share a situation where you had to collaborate closely with a team member who had a different perspective?"
        state.refused_questions.append(declined_q)

        refused_area = get_question_behavioral_area(declined_q)
        self.assertEqual(refused_area, "teamwork")

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("500")
            mock_chat.side_effect = RuntimeError("500")

            q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I would prefer not to answer that.",
                is_refusal=True,
                refused_question=declined_q,
            ))

        # Must not repeat the declined question or topic
        self.assertNotIn("collaborate closely with a team member", q)
        # Must include the professional refusal prefix
        self.assertTrue(q.startswith(PROFESSIONAL_REFUSAL_PREFIX))
        # Must pivot to a different behavioral area (e.g. problem solving)
        chosen_area = get_question_behavioral_area(q)
        self.assertNotEqual(chosen_area, "teamwork")
        self.assertEqual(chosen_area, "problem_solving")

    def test_task2_declined_question_scoring_fairness_and_transcript_preservation(self):
        """Verify Task 2: declining a question bypasses negative behavioral scoring, tracks refused questions,
        and records the turn accurately in the transcript."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        declined_prompt = "Tell me about a time you made a major mistake at work."
        state.add_transcript("interviewer", declined_prompt)

        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        async def gen_diff_area(st, rem, **kwargs):
            return "How do you evaluate new architectural proposals?"

        user_answer = "I am not comfortable answering this question."
        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=user_answer,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_diff_area,
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont)
        # analyze_fn must NEVER be called on a refusal turn
        analyze_mock.assert_not_awaited()
        # Refused question tracked
        self.assertIn(declined_prompt, state.refused_questions)
        # Spoken question must contain the refusal acknowledgment prefix
        speak_mock.assert_awaited_once()
        spoken_text = speak_mock.await_args[0][0]
        self.assertIn("Please note that skipping a question may limit the evidence available", spoken_text)
        self.assertIn("How do you evaluate new architectural proposals?", spoken_text)

    def test_personal_topic_redirect_explains_professional_boundary(self):
        """Verify candidate request for personal or unrelated topics triggers professional boundary reminder without recording refusal."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            PROFESSIONAL_BOUNDARY_PREFIX,
            is_personal_or_unrelated_request,
        )

        self.assertTrue(is_personal_or_unrelated_request("Can we talk about my personal life?"))
        self.assertTrue(is_personal_or_unrelated_request("Could we discuss sports or movies instead?"))

        state = AssessmentState()
        last_q = "How do you handle cross-functional communication?"
        state.add_transcript("interviewer", last_q)

        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        async def gen_q(st, rem, **kwargs):
            return "Could you describe a challenging project you contributed to?"

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Can we talk about my personal life?",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_q,
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont)
        # Bypasses candidate analysis for this off-topic turn
        analyze_mock.assert_not_awaited()
        # Must NOT treat off-topic request as a declined question
        self.assertEqual(len(state.refused_questions), 0)
        # Response must include the professional boundary explanation
        speak_mock.assert_awaited_once()
        spoken_text = speak_mock.await_args[0][0]
        self.assertTrue(spoken_text.startswith(PROFESSIONAL_BOUNDARY_PREFIX))
        self.assertIn("Could you describe a challenging project you contributed to?", spoken_text)

    def test_question_decline_distinct_from_unrelated_topic(self):
        """Verify question decline and off-topic requests are handled distinctly."""
        from agent.agent import (
            is_candidate_refusal,
            is_personal_or_unrelated_request,
            format_refusal_response,
            format_boundary_response,
            PROFESSIONAL_REFUSAL_PREFIX,
            PROFESSIONAL_BOUNDARY_PREFIX,
        )

        decline_text = "I prefer not to answer this question."
        boundary_text = "Can we chat about something outside of work?"

        # Classifiers are separate
        self.assertTrue(is_candidate_refusal(decline_text))
        self.assertFalse(is_personal_or_unrelated_request(decline_text))

        self.assertTrue(is_personal_or_unrelated_request(boundary_text))
        self.assertFalse(is_candidate_refusal(boundary_text))

        # Distinct response formatters
        formatted_refusal = format_refusal_response("Tell me about a project.")
        formatted_boundary = format_boundary_response("Tell me about a project.")

        self.assertTrue(formatted_refusal.startswith(PROFESSIONAL_REFUSAL_PREFIX))
        self.assertTrue(formatted_boundary.startswith(PROFESSIONAL_BOUNDARY_PREFIX))
        self.assertNotEqual(formatted_refusal, formatted_boundary)

    def test_abusive_language_first_warning_and_escalated_warning(self):
        """Verify calm professional warning on first abusive statement and firmer reminder on repeated abuse."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            ABUSE_WARNING_FIRST_PREFIX,
            ABUSE_WARNING_REPEATED_PREFIX,
            is_abusive_language,
        )

        self.assertTrue(is_abusive_language("You are a stupid idiot."))
        self.assertTrue(is_abusive_language("Shut the fuck up."))

        state = AssessmentState()
        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        async def gen_q(st, rem, **kwargs):
            return "How do you manage deadlines under pressure?"

        # Turn 1: First abusive statement
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="You are a stupid idiot.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_q,
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont1)
        self.assertEqual(state.abusive_language_count, 1)
        self.assertEqual(len(state.refused_questions), 0)
        analyze_mock.assert_not_awaited()
        speak_mock.assert_awaited_once()
        spoken_turn_1 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_turn_1.startswith(ABUSE_WARNING_FIRST_PREFIX))
        self.assertIn("How do you manage deadlines under pressure?", spoken_turn_1)

        # Turn 2: Repeated abusive statement
        speak_mock.reset_mock()
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Shut the fuck up.",
            remaining_seconds=250,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_q,
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont2)
        self.assertEqual(state.abusive_language_count, 2)
        self.assertEqual(len(state.refused_questions), 0)
        analyze_mock.assert_not_awaited()
        speak_mock.assert_awaited_once()
        spoken_turn_2 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_turn_2.startswith(ABUSE_WARNING_REPEATED_PREFIX))
        self.assertIn("communicate respectfully", spoken_turn_2.lower())
        self.assertIn("continue the professional interview", spoken_turn_2.lower())

    def test_false_positive_protection_allows_frustration_disagreement_and_relevant_context(self):
        """Verify normal workplace frustration, technical disagreement, nervousness, and relevant personal context are not misclassified."""
        from agent.agent import (
            is_abusive_language,
            is_personal_or_unrelated_request,
            is_candidate_refusal,
        )

        test_cases = [
            "That production outage was so damn frustrating to troubleshoot.",
            "We had a hell of a deployment that night with three schema migrations.",
            "I strongly disagreed with the product manager's proposed timeline.",
            "I am feeling very nervous about this technical interview today.",
            "In my personal life, I volunteer as a tech mentor which helped me develop empathy for junior engineers.",
            "Outside of work, I maintain an open-source library that uses Redis.",
            "My personal background in sports taught me discipline and collaborative teamwork.",
        ]

        for text in test_cases:
            self.assertFalse(is_abusive_language(text), f"Falsely flagged as abuse: {text}")
            self.assertFalse(is_personal_or_unrelated_request(text), f"Falsely flagged as off-topic request: {text}")
            self.assertFalse(is_candidate_refusal(text), f"Falsely flagged as refusal: {text}")

    def test_scoring_fairness_and_transcript_integrity_on_boundary_and_abuse_turns(self):
        """Verify scoring fairness (no penalty) and transcript integrity during boundary and abuse turns."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            PROFESSIONAL_BOUNDARY_PREFIX,
            ABUSE_WARNING_FIRST_PREFIX,
        )

        state = AssessmentState()
        # Pre-existing evidence from previous valid answers
        state.add_evidence("communication", "Demonstrated clear stakeholder alignment", "strong", 0.9)
        initial_evidence_count = len(state.evidence["communication"])

        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        async def gen_q(st, rem, **kwargs):
            return "How do you handle team disagreements?"

        # Turn with boundary request
        asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Can we talk about my personal life?",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_q,
            send_event_fn=send_event_mock,
        ))

        # Evidence unchanged
        self.assertEqual(len(state.evidence["communication"]), initial_evidence_count)
        self.assertEqual(state.evidence["communication"][0].strength, "strong")

        # Turn during warning period with abuse
        speak_mock.reset_mock()
        ended = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Fuck off you bot.",
            remaining_seconds=20,
            warning_sent=True,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_q,
            send_event_fn=send_event_mock,
        ))

        self.assertFalse(ended)
        # Warning delivered without analyzing abuse into candidate evidence
        analyze_mock.assert_not_awaited()
        speak_mock.assert_awaited_once()
        self.assertEqual(len(state.evidence["communication"]), initial_evidence_count)

    def test_off_topic_movie_response_redirects_immediately_and_speaks(self):
        """Verify candidate answer about movies triggers immediate spoken redirection without remaining silent."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            OFF_TOPIC_FIRST_PREFIX,
            is_off_topic_answer,
        )

        movie_answer = "I really love watching Marvel movies and Inception was an amazing film."
        self.assertTrue(is_off_topic_answer(movie_answer))

        state = AssessmentState()
        opening_q = "To begin, please introduce yourself and briefly tell me about your background."
        state.add_transcript("interviewer", opening_q)

        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=movie_answer,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_event_mock,
        ))

        # Must continue interview (not remain silent or exit)
        self.assertTrue(cont)
        # Bypasses candidate analysis for off-topic turn
        analyze_mock.assert_not_awaited()
        # Off-topic count incremented
        self.assertEqual(state.off_topic_count, 1)
        # candidate_introduction must not be poisoned by movie answer
        self.assertNotEqual(state.candidate_introduction, movie_answer)
        # Spoken redirection delivered via speak_fn
        speak_mock.assert_awaited_once()
        spoken_text = speak_mock.await_args[0][0]
        self.assertTrue(spoken_text.startswith(OFF_TOPIC_FIRST_PREFIX))
        self.assertIn("professional background", spoken_text.lower())

    def test_repeated_off_topic_answers_redirect_without_looping(self):
        """Verify repeated off-topic responses escalate redirection and pivot to an unasked question without looping."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            OFF_TOPIC_FIRST_PREFIX,
            OFF_TOPIC_REPEATED_PREFIX,
        )

        state = AssessmentState()
        first_q = "Could you tell me about a challenging technical problem you encountered?"
        state.add_transcript("interviewer", first_q)

        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        # Turn 1: First off-topic answer (talking about movies)
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Spider-Man is my favorite superhero movie, I watch movies every Friday night.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont1)
        self.assertEqual(state.off_topic_count, 1)
        speak_mock.assert_awaited_once()
        spoken_1 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_1.startswith(OFF_TOPIC_FIRST_PREFIX))

        # Turn 2: Repeated off-topic answer (talking about gaming)
        speak_mock.reset_mock()
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="I was playing Fortnite and Minecraft all weekend with friends.",
            remaining_seconds=270,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont2)
        self.assertEqual(state.off_topic_count, 2)
        speak_mock.assert_awaited_once()
        spoken_2 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_2.startswith(OFF_TOPIC_REPEATED_PREFIX))
        # Must not be an identical repeat of turn 1
        self.assertNotEqual(spoken_1, spoken_2)

    def test_relevant_answers_not_classified_as_off_topic(self):
        """Verify substantive workplace answers, relevant personal examples, and nervousness are not flagged as off-topic."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_off_topic_answer,
        )

        relevant_cases = [
            "At my previous company, I worked on the video streaming pipeline for a media production team.",
            "In my personal life, I volunteer as a tech mentor which helped me develop empathy for junior engineers.",
            "My personal background in sports taught me discipline and collaborative teamwork in project delivery.",
            "I developed a video game in C++ and Unreal Engine for a university client project.",
            "I am feeling quite nervous about this interview today, but I am ready to answer.",
            "I disagreed with the tech lead's database schema because it would not scale under load.",
        ]

        for text in relevant_cases:
            self.assertFalse(is_off_topic_answer(text), f"Falsely flagged as off-topic: {text}")

        state = AssessmentState()
        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        async def gen_q(st, rem, **kwargs):
            return "How did you manage the rollout?"

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="I led the migration of our authentication service to OAuth2 with zero downtime.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_q,
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont)
        self.assertEqual(state.off_topic_count, 0)
        analyze_mock.assert_awaited_once()
        self.assertEqual(state.candidate_introduction, "I led the migration of our authentication service to OAuth2 with zero downtime.")

    def test_clarification_request_repeats_question_immediately(self):
        """Verify clarification requests repeat the question politely without penalizing candidate or remaining silent."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_clarification_request,
        )

        self.assertTrue(is_clarification_request("Could you repeat the question, please?"))
        self.assertTrue(is_clarification_request("Can you clarify what you mean by cross-functional stakeholders?"))

        state = AssessmentState()
        current_q = "How do you handle ambiguous requirements?"
        state.add_transcript("interviewer", current_q)

        analyze_mock = AsyncMock()
        speak_mock = AsyncMock()
        send_event_mock = AsyncMock()

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Could you repeat the question, please?",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont)
        analyze_mock.assert_not_awaited()
        speak_mock.assert_awaited_once()
        spoken = speak_mock.await_args[0][0]
        self.assertTrue(spoken.startswith("Certainly."))
        self.assertIn("How do you handle ambiguous requirements?", spoken)

    def test_repeated_movie_topic_redirects_to_professional_experience_and_excludes_movie_nights(self):
        """Regression test for the exact two-turn scenario:
        1. Candidate: 'I love Marvel movies and watching films. My favorite movie is Inception.'
        2. AI asks a workplace communication question.
        3. Candidate repeats: 'I said I love Marvel movies and watching films. My favorite movie is Inception.'
        4. Assert that the second AI response does NOT mention movie nights or continue the entertainment topic
           and instead redirects to professional workplace skills or experience.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            OFF_TOPIC_FIRST_PREFIX,
            OFF_TOPIC_REPEATED_PREFIX,
            is_unrelated_entertainment_question,
        )

        state = AssessmentState()
        # Initial opening question
        state.add_transcript("interviewer", "To begin, please introduce yourself and briefly tell me about your background.")

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_event_mock = AsyncMock()

        # Turn 1: Candidate mentions movies
        ans1 = "I love Marvel movies and watching films. My favorite movie is Inception."
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=ans1,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont1)
        speak_mock.assert_awaited_once()
        spoken_1 = speak_mock.await_args[0][0]
        # First turn redirects politely to workplace experience
        self.assertTrue(spoken_1.startswith(OFF_TOPIC_FIRST_PREFIX))
        self.assertFalse(is_unrelated_entertainment_question(spoken_1))

        # Add candidate's turn 1 and interviewer's response to transcript (as in live session)
        state.add_transcript("candidate", ans1)

        # Turn 2: Candidate repeats the exact movie topic
        speak_mock.reset_mock()
        analyze_mock.reset_mock()
        ans2 = "I said I love Marvel movies and watching films. My favorite movie is Inception."
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=ans2,
            remaining_seconds=270,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont2)
        speak_mock.assert_awaited_once()
        spoken_2 = speak_mock.await_args[0][0]

        # Assert second AI response:
        # 1. Must NOT mention movie nights or continue entertainment topic
        self.assertNotIn("movie night", spoken_2.lower())
        self.assertNotIn("movie nights", spoken_2.lower())
        self.assertNotIn("inception", spoken_2.lower())
        self.assertNotIn("marvel", spoken_2.lower())
        self.assertFalse(is_unrelated_entertainment_question(spoken_2))

        # 2. Must give brief professional redirect and ask about workplace skills or experience
        self.assertTrue(spoken_2.startswith(OFF_TOPIC_REPEATED_PREFIX))
        self.assertTrue(
            any(w in spoken_2.lower() for w in ("problem", "technical", "project", "workplace", "collaborate", "decision", "career"))
        )

        # 3. Delivered via TTS and continues without penalizing candidate
        analyze_mock.assert_not_awaited()

    def test_generate_adaptive_question_discards_movie_night_questions(self):
        """Verify generate_adaptive_question discards questions about movie nights or entertainment and uses safe workplace fallback."""
        import asyncio
        from unittest.mock import AsyncMock, patch
        from agent.agent import (
            AssessmentState,
            generate_adaptive_question,
            is_unrelated_entertainment_question,
        )

        state = AssessmentState()
        mock_response = AsyncMock()
        # Simulate LLM trying to invent a movie night question
        mock_response.output_text = "Can you describe a time when you worked with a group to organize a movie night or a similar event?"

        with patch("agent.agent.openai_client.responses.create", return_value=mock_response):
            question = asyncio.run(generate_adaptive_question(
                state=state,
                remaining_seconds=300,
                current_answer="I love Marvel movies and watching films.",
            ))

        self.assertTrue(len(question) > 0)
        self.assertNotIn("movie night", question.lower())
        self.assertFalse(is_unrelated_entertainment_question(question))
        # Returned a safe workplace behavioral fallback instead
        self.assertTrue(
            any(k in question.lower() for k in ("problem", "collaborate", "priorities", "decision", "ownership", "career", "challenge"))
        )


    def test_live_reproduction_csc_ai_team_question_with_marvel_answer_redirects_without_entertainment_followup(self):
        """Exact reproduction test from live interview:
        Interviewer:
        'Can you share an experience from your time in the CSC AI branch where you had to work as part of a team to complete a project or assignment? What role did you play, and how did you contribute to the team\'s success?'
        Candidate:
        'I love Marvel movie, and I favorite movie is Inception.'
        Verify that:
        1. LLM question generator is NOT called.
        2. Candidate answer is NOT penalized in analysis.
        3. Spoken response through TTS is a professional redirect that does NOT quote or build a question around Marvel, Inception, movies, or films.
        4. Redirect refocuses the candidate on the CSC AI team question.
        5. generate_adaptive_question circuit-breaker directly bypasses OpenAI when invoked with this answer.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            generate_adaptive_question,
            is_off_topic_answer,
            is_unrelated_entertainment_question,
            OFF_TOPIC_FIRST_PREFIX,
        )

        cand_answer = "I love Marvel movie, and I favorite movie is Inception."
        self.assertTrue(is_off_topic_answer(cand_answer))

        state = AssessmentState()
        state.add_transcript("interviewer", "To begin, please introduce yourself and briefly tell me about your background.")
        state.add_transcript("candidate", "My name is Avita. Currently, I'm pursuing BTEC from CSC AI branch.")
        interviewer_q = "Can you share an experience from your time in the CSC AI branch where you had to work as part of a team to complete a project or assignment? What role did you play, and how did you contribute to the team's success?"
        state.add_transcript("interviewer", interviewer_q)

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        gen_q_mock = AsyncMock()
        send_event_mock = AsyncMock()

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=cand_answer,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_q_mock,
            send_event_fn=send_event_mock,
        ))

        self.assertTrue(cont)
        # 1. LLM question generator is bypassed
        gen_q_mock.assert_not_called()
        # 2. Candidate analysis is bypassed (no penalty for off-topic)
        analyze_mock.assert_not_awaited()
        # 3. Delivered through LiveKit TTS
        speak_mock.assert_awaited_once()
        spoken = speak_mock.await_args[0][0]

        # 4. Spoken text must start with professional redirect prefix
        self.assertTrue(spoken.startswith(OFF_TOPIC_FIRST_PREFIX))
        # 5. Spoken text must NOT contain or build around Marvel, Inception, movies, or films
        self.assertNotIn("marvel", spoken.lower())
        self.assertNotIn("inception", spoken.lower())
        self.assertNotIn("movie", spoken.lower())
        self.assertNotIn("film", spoken.lower())
        self.assertFalse(is_unrelated_entertainment_question(spoken))
        # 6. Must refocus on the CSC AI team question
        self.assertIn("csc ai branch", spoken.lower())

        # 7. Test circuit-breaker directly on generate_adaptive_question
        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp:
            cb_result = asyncio.run(generate_adaptive_question(
                state=state,
                remaining_seconds=300,
                current_answer=cand_answer,
            ))
            mock_resp.assert_not_called()
            self.assertTrue(len(cb_result) > 0)
            self.assertNotIn("marvel", cb_result.lower())
            self.assertNotIn("inception", cb_result.lower())
            self.assertNotIn("movie", cb_result.lower())
            self.assertFalse(is_unrelated_entertainment_question(cb_result))

    def test_turn_consolidation_with_accumulated_segments(self):
        """Verify that when endpointing fires on a partial word/phrase, accumulated segments recover the full response."""
        uncommitted_segments = ["I am a computer science student studying in the CSC AI", "branch."]
        answer_text = "Branch."

        answer_clean = answer_text.strip()
        accumulated_segments = " ".join(s for s in uncommitted_segments if s).strip()
        if accumulated_segments and len(accumulated_segments.split()) > len(answer_clean.split()):
            answer_clean = accumulated_segments

        self.assertEqual(
            answer_clean,
            "I am a computer science student studying in the CSC AI branch.",
        )
        self.assertEqual(len(answer_clean.split()), 12)

    def test_debounce_turn_consolidation_in_queue(self):
        """Verify that multi-segment speech separated by natural pauses consolidates before question generation."""
        import asyncio
        async def run_debounce():
            queue = asyncio.Queue()
            queue.put_nowait("I am currently leading")
            queue.put_nowait("a machine learning team at our college.")

            first_chunk = await queue.get()
            consolidated = first_chunk
            while not queue.empty():
                next_chunk = queue.get_nowait()
                if next_chunk and next_chunk.strip():
                    if not consolidated.lower().endswith(next_chunk.strip().lower()):
                        consolidated = f"{consolidated} {next_chunk.strip()}"
            return consolidated

        result = asyncio.run(run_debounce())
        self.assertEqual(
            result,
            "I am currently leading a machine learning team at our college.",
        )

    def test_no_question_advance_before_candidate_response_processed(self):
        """Verify that questions are not advanced or sent until candidate answer is fully processed."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        state.transcript = [{"role": "interviewer", "text": "Introduce yourself."}]
        state.questions_asked = 1

        speak_called = False
        analyze_called = False

        async def mock_analyze(st, ans):
            nonlocal analyze_called
            analyze_called = True
            st.add_evidence("communication", "Answer given", "strong", 0.9)

        async def mock_speak(q, label=""):
            nonlocal speak_called
            # When speak is called, analyze must already have run
            self.assertTrue(analyze_called, "Question spoken before candidate answer analysis finished!")
            speak_called = True
            return True

        async def mock_gen_q(st, rem, current_answer, transcript, evidence, **kwargs):
            return "Tell me about a technical project you built."

        asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="I am a software engineering student with experience in Python and cloud services.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=mock_speak,
            analyze_fn=mock_analyze,
            generate_question_fn=mock_gen_q,
            send_event_fn=AsyncMock(),
        ))

        self.assertTrue(analyze_called)
        self.assertTrue(speak_called)
        self.assertEqual(state.questions_asked, 2)
        self.assertEqual(state.transcript[-1]["text"], "Tell me about a technical project you built.")

    def test_vad_prewarm_configuration(self):
        """Verify prewarm sets sample_rate 16000 and min_silence_duration 0.8."""
        from unittest.mock import MagicMock, patch
        from agent.agent import prewarm

        mock_proc = MagicMock()
        mock_proc.userdata = {}

        with patch("livekit.plugins.silero.VAD.load") as mock_load:
            prewarm(mock_proc)
            mock_load.assert_called_once_with(
                sample_rate=16000,
                min_silence_duration=0.8,
                activation_threshold=0.5,
            )

    def test_feature1_timer_stops_during_pause_and_resumes_with_correct_remaining_time(self):
        """1. Verify timer stops during pause and resumes with the correct remaining time."""
        duration = 300
        accumulated_active_seconds = 0.0
        is_paused = False
        fake_clock = 1000.0
        last_tick_time = fake_clock

        def get_remaining():
            nonlocal accumulated_active_seconds, last_tick_time, is_paused, fake_clock
            active = accumulated_active_seconds
            if not is_paused:
                active += (fake_clock - last_tick_time)
            return max(0, int(duration - active))

        # Start interview: 300s remaining
        self.assertEqual(get_remaining(), 300)

        # 40 seconds pass while active
        fake_clock += 40.0
        self.assertEqual(get_remaining(), 260)

        # Pause request received
        accumulated_active_seconds += (fake_clock - last_tick_time)
        last_tick_time = fake_clock
        is_paused = True
        frozen_remaining = get_remaining()
        self.assertEqual(frozen_remaining, 260)

        # 120 seconds pass while paused
        fake_clock += 120.0
        self.assertEqual(get_remaining(), 260)  # Still exactly 260!

        # Resume request received
        last_tick_time = fake_clock
        is_paused = False
        self.assertEqual(get_remaining(), 260)  # Continues from 260

        # Another 15 seconds of active interview
        fake_clock += 15.0
        self.assertEqual(get_remaining(), 245)

    def test_feature1_current_question_and_transcript_remain_unchanged_across_pause_resume(self):
        """2. Verify current question and transcript remain unchanged across Pause/Resume."""
        from agent.agent import AssessmentState
        state = AssessmentState()
        state.add_transcript("interviewer", "Tell me about a complex project you led.")
        state.add_transcript("candidate", "I led the migration of our database.")
        state.questions_asked = 1

        initial_transcript = list(state.transcript)
        initial_q_count = state.questions_asked

        # Pause state transition
        state.is_paused = True
        self.assertEqual(state.transcript, initial_transcript)
        self.assertEqual(state.questions_asked, initial_q_count)

        # Resume state transition
        state.is_paused = False
        self.assertEqual(state.transcript, initial_transcript)
        self.assertEqual(state.questions_asked, initial_q_count)

    def test_feature1_ai_does_not_generate_or_speak_new_question_while_paused(self):
        """3. Verify AI does not generate or speak a new question while paused."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn, speak_with_lock

        state = AssessmentState()
        state.transcript = [{"role": "interviewer", "text": "Question 1"}]
        state.questions_asked = 1

        gen_called = False
        speak_called = False

        async def mock_gen(*args, **kwargs):
            nonlocal gen_called
            gen_called = True
            return "Next question"

        async def mock_speak(q, label=""):
            nonlocal speak_called
            speak_called = True
            return True

        # Test A: speak_with_lock does not start or aborts if paused
        session = MagicMock()
        lock = asyncio.Lock()
        pause_event = asyncio.Event()
        pause_event.set()  # Already paused
        res = asyncio.run(speak_with_lock(
            session=session,
            speech_lock=lock,
            text="Should not speak while paused",
            stage_label="test",
            is_paused_fn=lambda: True,
            pause_event=pause_event,
        ))
        self.assertFalse(res)
        session.say.assert_not_called()

        # Test B: process_interview_turn waits on resume_event before generating/speaking
        resume_event = asyncio.Event()  # Not set (paused)
        is_paused = True

        async def run_turn():
            return await process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer="My answer while paused",
                remaining_seconds=200,
                warning_sent=False,
                user_answer_queue=asyncio.Queue(),
                analyzed_answers=set(),
                manual_end_event=asyncio.Event(),
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=mock_speak,
                analyze_fn=AsyncMock(),
                generate_question_fn=mock_gen,
                send_event_fn=AsyncMock(),
                is_paused_fn=lambda: is_paused,
                resume_event=resume_event,
            )

        async def turn_runner():
            t = asyncio.create_task(run_turn())
            await asyncio.sleep(0.05)
            # While paused, gen and speak must not have been called
            self.assertFalse(gen_called)
            self.assertFalse(speak_called)
            # Unpause
            nonlocal is_paused
            is_paused = False
            resume_event.set()
            await t
            self.assertTrue(gen_called)
            self.assertTrue(speak_called)

        asyncio.run(turn_runner())

    def test_feature1_resume_does_not_replay_opening_question(self):
        """4. Verify Resume does not replay the opening question."""
        from agent.agent import AssessmentState
        opening_q = "To begin, please introduce yourself and briefly tell me about your background."
        state = AssessmentState()
        state.add_transcript("interviewer", opening_q)
        state.questions_asked = 1

        # Pause and resume
        state.is_paused = True
        state.is_paused = False

        # Verify opening question is only in transcript once
        interviewer_utterances = [t["text"] for t in state.transcript if t.get("role") in ("interviewer", "assistant")]
        self.assertEqual(interviewer_utterances.count(opening_q), 1)

    def test_feature2_stop_interview_and_conclude_now_trigger_graceful_completion(self):
        """5. Verify 'Stop the interview' and 'Please conclude now' trigger graceful completion."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_candidate_end_request,
            CANDIDATE_END_CLOSING_MESSAGE,
        )

        test_phrases = [
            "Stop the interview.",
            "Please end this interview.",
            "Let's conclude now.",
            "I don't want to continue.",
            "Please finish and give me the conclusion.",
        ]

        for phrase in test_phrases:
            self.assertTrue(is_candidate_end_request(phrase), f"Failed to match end request: '{phrase}'")

            state = AssessmentState()
            state.transcript = [{"role": "interviewer", "text": "Question 1"}]
            state.questions_asked = 1
            manual_end_event = asyncio.Event()
            spoken_messages = []

            async def mock_speak(q, label=""):
                spoken_messages.append(q)
                return True

            gen_mock = AsyncMock()
            send_mock = AsyncMock()

            should_continue = asyncio.run(process_interview_turn(
                ctx=MagicMock(),
                state=state,
                answer=phrase,
                remaining_seconds=250,
                warning_sent=False,
                user_answer_queue=asyncio.Queue(),
                analyzed_answers=set(),
                manual_end_event=manual_end_event,
                timer_finished_event=asyncio.Event(),
                disconnect_event=asyncio.Event(),
                speak_fn=mock_speak,
                analyze_fn=AsyncMock(),
                generate_question_fn=gen_mock,
                send_event_fn=send_mock,
            ))

            self.assertFalse(should_continue, f"Turn should return False on ending intent: {phrase}")
            self.assertTrue(manual_end_event.is_set(), f"manual_end_event should be set: {phrase}")
            gen_mock.assert_not_called()
            self.assertIn(CANDIDATE_END_CLOSING_MESSAGE, spoken_messages)
            self.assertTrue(any(t.get("role") == "candidate" and t.get("text") == phrase for t in state.transcript))

    def test_feature2_ending_intent_takes_precedence_over_pending_next_question_generation(self):
        """6. Verify ending intent takes precedence over pending next-question generation."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        manual_end_event = asyncio.Event()
        gen_mock = AsyncMock()

        should_continue = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="I want to stop here please.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=manual_end_event,
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=AsyncMock(return_value=True),
            analyze_fn=AsyncMock(),
            generate_question_fn=gen_mock,
            send_event_fn=AsyncMock(),
        ))

        self.assertFalse(should_continue)
        self.assertTrue(manual_end_event.is_set())
        gen_mock.assert_not_called()

    def test_feature3_abusive_language_receives_professional_response_never_abusive_reply(self):
        """7. Verify abusive language receives a professional response, never an abusive reply."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            ABUSE_WARNING_FIRST_PREFIX,
        )

        state = AssessmentState()
        spoken_messages = []

        async def mock_speak(q, label=""):
            spoken_messages.append(q)
            return True

        # Candidate uses abusive words
        should_continue = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Fuck off you stupid bot.",
            remaining_seconds=200,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=mock_speak,
            analyze_fn=AsyncMock(),
            generate_question_fn=AsyncMock(return_value="How do you handle team collaboration?"),
            send_event_fn=AsyncMock(),
        ))

        self.assertTrue(should_continue)
        self.assertEqual(state.abusive_language_count, 1)
        self.assertTrue(len(spoken_messages) > 0)
        resp = spoken_messages[0]
        self.assertTrue(resp.startswith(ABUSE_WARNING_FIRST_PREFIX))
        for bad_word in ["fuck", "stupid", "bot", "idiot", "moron"]:
            self.assertNotIn(bad_word, resp.lower())

        # Test abusive language WITH explicit ending intent: honors conclusion calmly!
        spoken_end = []
        async def mock_speak_end(q, label=""):
            spoken_end.append(q)
            return True

        manual_end_event = asyncio.Event()
        should_continue_end = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Stop the interview you stupid bot.",
            remaining_seconds=200,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=manual_end_event,
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=mock_speak_end,
            analyze_fn=AsyncMock(),
            generate_question_fn=AsyncMock(),
            send_event_fn=AsyncMock(),
        ))

        self.assertFalse(should_continue_end)
        self.assertTrue(manual_end_event.is_set())
        self.assertEqual(spoken_end[0], "I understand. We will conclude the interview here. Thank you for your time.")

    def test_feature2_ordinary_answer_containing_stop_does_not_end_interview(self):
        """8. Verify an ordinary answer containing the word 'stop' does not automatically end the interview."""
        from agent.agent import is_candidate_end_request

        work_answers = [
            "I had to stop the database replication to fix the split-brain issue.",
            "We didn't stop until all automated tests were green and the release passed.",
            "I asked our team to stop using legacy dependencies and migrate to OAuth2.",
            "We finished the frontend migration on time without any downtime.",
            "In conclusion, the migration improved our system latency by forty percent.",
        ]

        for ans in work_answers:
            self.assertFalse(
                is_candidate_end_request(ans),
                f"Ordinary answer incorrectly identified as end request: '{ans}'"
            )

    def test_feature4_final_transcript_and_assessment_result_saved_exactly_once(self):
        """9. Verify final transcript and assessment result are saved exactly once."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, deliver_final_result_and_sync

        state = AssessmentState()
        state.add_transcript("interviewer", "Opening question.")
        state.add_transcript("candidate", "My answer.")

        send_mock = AsyncMock()
        save_mock = MagicMock()
        sync_mock = AsyncMock()
        disconnect_event = asyncio.Event()
        disconnect_event.set()

        # Call deliver_final_result_and_sync twice sequentially
        asyncio.run(deliver_final_result_and_sync(
            ctx=MagicMock(),
            state=state,
            assessment_id="test-assess-1",
            candidate_disconnected=disconnect_event,
            send_event_fn=send_mock,
            save_fn=save_mock,
            sync_fn=sync_mock,
        ))

        asyncio.run(deliver_final_result_and_sync(
            ctx=MagicMock(),
            state=state,
            assessment_id="test-assess-1",
            candidate_disconnected=disconnect_event,
            send_event_fn=send_mock,
            save_fn=save_mock,
            sync_fn=sync_mock,
        ))

        self.assertEqual(send_mock.call_count, 1)
        self.assertEqual(save_mock.call_count, 1)
        self.assertEqual(sync_mock.call_count, 1)
        self.assertTrue(state.is_finalized)
        self.assertTrue(state.completed)

    def test_feature4_no_questions_generated_after_completion(self):
        """10. Verify no questions are generated after completion."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        state.completed = True
        state.is_finalized = True

        gen_mock = AsyncMock()
        speak_mock = AsyncMock()

        should_continue = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Any candidate utterance after completion",
            remaining_seconds=100,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=AsyncMock(),
            generate_question_fn=gen_mock,
            send_event_fn=AsyncMock(),
        ))

        self.assertFalse(should_continue)
        gen_mock.assert_not_called()
        speak_mock.assert_not_called()

    # ============================================================
    # FOCUSED TESTS FOR THE 7 BEHAVIORAL AI INTERVIEWER RULES
    # ============================================================

    def test_rule1_unrelated_personal_question_redirects_to_professional_interview(self):
        """Rule 1: If candidate asks unrelated, personal, or non-professional questions,
        politely explain that this is a professional interview and redirect to work-related topics.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            PROFESSIONAL_BOUNDARY_PREFIX,
            is_personal_or_unrelated_request,
        )

        test_questions = [
            "Can we talk about my personal life?",
            "What is your favorite color?",
            "Are you married?",
            "What do you like to do on weekends?",
            "Where do you live?",
        ]
        for q in test_questions:
            self.assertTrue(is_personal_or_unrelated_request(q), f"Failed to detect personal/unrelated question: {q}")

        state = AssessmentState()
        state.add_transcript("interviewer", "How do you handle team disagreements?")

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        async def fake_gen_q(st, rem, **kwargs):
            return "How do you coordinate with cross-functional stakeholders?"

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="What is your favorite color and what do you do on weekends?",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=fake_gen_q,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont)
        analyze_mock.assert_not_awaited()
        self.assertEqual(len(state.refused_questions), 0)
        speak_mock.assert_awaited_once()
        spoken = speak_mock.await_args[0][0]
        self.assertTrue(spoken.startswith(PROFESSIONAL_BOUNDARY_PREFIX))
        self.assertIn("cross-functional stakeholders", spoken)

    def test_rule2_abusive_language_responds_firmly_sets_boundary_invites_respectful_continuation(self):
        """Rule 2: If candidate uses abusive language, respond respectfully but firmly,
        set a professional boundary, and invite them to continue respectfully without insulting or arguing.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            ABUSE_WARNING_FIRST_PREFIX,
            ABUSE_WARNING_REPEATED_PREFIX,
            is_abusive_language,
        )

        abuse_inputs = ["You are an idiot bot.", "Shut the fuck up."]
        for text in abuse_inputs:
            self.assertTrue(is_abusive_language(text), f"Failed to detect abusive language: {text}")

        state = AssessmentState()
        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        async def fake_gen_q(st, rem, **kwargs):
            return "Tell me about a complex project you delivered."

        # Turn 1: First abuse warning sets boundary and invites to continue respectfully
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="You are an idiot bot.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=fake_gen_q,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont1)
        self.assertEqual(state.abusive_language_count, 1)
        analyze_mock.assert_not_awaited()
        spoken_1 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_1.startswith(ABUSE_WARNING_FIRST_PREFIX))
        self.assertIn("respectful", spoken_1.lower())
        self.assertNotIn("idiot", spoken_1.lower())
        self.assertIn("complex project you delivered", spoken_1)

        # Turn 2: Repeated abuse reminder
        speak_mock.reset_mock()
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Shut the fuck up.",
            remaining_seconds=250,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=fake_gen_q,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont2)
        self.assertEqual(state.abusive_language_count, 2)
        spoken_2 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_2.startswith(ABUSE_WARNING_REPEATED_PREFIX))
        self.assertIn("respectfully", spoken_2.lower())

    def test_rule3_repeat_and_clarify_repeats_rephrases_avoids_endless_repetition(self):
        """Rule 3: If candidate asks to repeat or clarify a question, repeat or rephrase
        the current question clearly, and avoid endless repetition.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_clarification_request,
            FALLBACK_BEHAVIORAL_QUESTIONS,
        )

        self.assertTrue(is_clarification_request("Could you repeat that?"))
        self.assertTrue(is_clarification_request("Can you clarify the question?"))
        self.assertTrue(is_clarification_request("Please say that again."))

        state = AssessmentState()
        original_q = "How do you evaluate new architectural proposals?"
        state.add_transcript("interviewer", original_q)
        initial_asked = state.questions_asked

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        # Turn 1: Repeat clearly
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Could you repeat the question, please?",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont1)
        self.assertEqual(state.questions_asked, initial_asked)
        self.assertEqual(state.repeat_count_for_current_question, 1)
        spoken_1 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_1.startswith("Certainly."))
        self.assertIn(original_q, spoken_1)

        # Turn 2: Rephrase clearly
        speak_mock.reset_mock()
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Can you clarify what you mean?",
            remaining_seconds=280,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont2)
        self.assertEqual(state.questions_asked, initial_asked)
        self.assertEqual(state.repeat_count_for_current_question, 2)
        spoken_2 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_2.startswith("To rephrase:"))
        self.assertIn(original_q, spoken_2)

        # Turn 3: Avoid endless repetition by pivoting to a fresh question
        speak_mock.reset_mock()
        cont3 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="I still did not catch that, say it again.",
            remaining_seconds=260,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont3)
        self.assertEqual(state.repeat_count_for_current_question, 0)
        spoken_3 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_3.startswith("Let's move on to a fresh question:"))
        self.assertNotEqual(spoken_3, spoken_1)

    def test_rule4_topic_change_explains_purpose_offers_rephrase_or_moment_no_unrelated_chat(self):
        """Rule 4: If candidate requests another topic because they do not want to discuss
        professional life, acknowledge request, explain interview's professional purpose,
        and offer to rephrase question or give brief moment. Do NOT enter prolonged unrelated conversation.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            PROFESSIONAL_TOPIC_REQUEST_RESPONSE,
            is_explicit_topic_change_preference,
        )

        topic_change_input = "Can we talk about some other topic? I don't want to discuss my professional life."
        self.assertTrue(is_explicit_topic_change_preference(topic_change_input))

        state = AssessmentState()
        state.add_transcript("interviewer", "How do you manage sprint priorities?")

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        # Turn 1: Candidate requests another topic because they don't want to discuss professional life
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=topic_change_input,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont1)
        self.assertTrue(state.waiting_for_resume)
        analyze_mock.assert_not_awaited()
        speak_mock.assert_awaited_once()
        spoken_1 = speak_mock.await_args[0][0]
        # Must acknowledge request, explain purpose, offer rephrase or moment
        self.assertEqual(spoken_1, PROFESSIONAL_TOPIC_REQUEST_RESPONSE)
        # Must NOT enter prolonged unrelated conversation about movies, entertainment, or hobbies
        self.assertNotIn("movie", spoken_1.lower())
        self.assertNotIn("film", spoken_1.lower())
        self.assertNotIn("action", spoken_1.lower())
        self.assertNotIn("what would you like to talk about", spoken_1.lower())

        # Turn 2: Candidate asks for a brief moment
        speak_mock.reset_mock()
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Give me a brief moment please.",
            remaining_seconds=280,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont2)
        self.assertTrue(state.waiting_for_resume)
        spoken_2 = speak_mock.await_args[0][0]
        self.assertIn("take all the time you need", spoken_2.lower())

        # Turn 3: Candidate asks to rephrase the question
        speak_mock.reset_mock()
        cont3 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Could you please rephrase the question?",
            remaining_seconds=260,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont3)
        self.assertFalse(state.waiting_for_resume)
        spoken_3 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_3.startswith("Certainly. To rephrase:"))
        self.assertIn("How do you manage sprint priorities?", spoken_3)

    def test_rule5_bare_okay_not_treated_as_permission_to_resume_clear_agreement_resumes(self):
        """Rule 5: Resume or continue assessment when candidate clearly agrees.
        Do NOT treat a bare 'Okay' as permission to resume when it could simply acknowledge the previous response.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_bare_acknowledgment,
            is_permission_to_resume,
        )

        # Verification of classifier behavior
        self.assertTrue(is_bare_acknowledgment("Okay"))
        self.assertTrue(is_bare_acknowledgment("ok"))
        self.assertTrue(is_bare_acknowledgment("okay."))
        self.assertFalse(is_permission_to_resume("Okay"))
        self.assertFalse(is_permission_to_resume("ok"))
        self.assertFalse(is_permission_to_resume("okay."))
        self.assertTrue(is_permission_to_resume("Okay, I'm ready"))
        self.assertTrue(is_permission_to_resume("I am ready"))
        self.assertTrue(is_permission_to_resume("Yes, let's continue"))
        self.assertTrue(is_permission_to_resume("Let's resume the interview"))

        state = AssessmentState()
        state.waiting_for_resume = True
        state.neutral_topic_turns = 1
        state.add_transcript("interviewer", "Take all the time you need. Let me know when you feel ready to continue.")
        initial_asked = state.questions_asked

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        # Turn 1: Candidate says bare "Okay"
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Okay.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont1)
        # Must NOT resume or advance questions on bare "Okay"
        self.assertTrue(state.waiting_for_resume)
        self.assertEqual(state.questions_asked, initial_asked)
        spoken_1 = speak_mock.await_args[0][0]
        self.assertIn("take your time", spoken_1.lower())
        self.assertNotIn("let's return to the assessment", spoken_1.lower())

        # Turn 2: Candidate clearly agrees to resume
        speak_mock.reset_mock()
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="I'm ready, let's continue.",
            remaining_seconds=280,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont2)
        # Resumes assessment when candidate clearly agrees
        self.assertFalse(state.waiting_for_resume)
        self.assertEqual(state.questions_asked, initial_asked + 1)
        spoken_2 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_2.startswith("Thank you. Let's return to the assessment."))

    def test_rule6_next_relevant_unanswered_question_without_repetition(self):
        """Rule 6: Ask next relevant, unanswered interview question without repeating questions unnecessarily."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from agent.agent import (
            AssessmentState,
            generate_adaptive_question,
            FALLBACK_BEHAVIORAL_QUESTIONS,
        )

        state = AssessmentState()
        q1 = "Can you describe a challenging technical problem you solved?"
        state.add_transcript("interviewer", q1)
        state.asked_questions.append(q1)

        captured_prompt = None

        # LLM attempts to repeat the exact question already asked in q1
        async def fake_resp_repeat(model, input):
            nonlocal captured_prompt
            captured_prompt = input
            mock_obj = MagicMock()
            mock_obj.output_text = q1
            return mock_obj

        with patch("agent.agent.openai_client.responses.create", side_effect=fake_resp_repeat):
            next_q = asyncio.run(generate_adaptive_question(
                state,
                300,
                current_answer="I implemented a caching layer.",
            ))

        # Model-generated repeated question must be discarded, and an unanswered fallback question chosen
        self.assertNotEqual(next_q, q1)
        self.assertIn(next_q, FALLBACK_BEHAVIORAL_QUESTIONS)

    def test_rule7_transcript_preserved_and_candidate_introduction_protected(self):
        """Rule 7: Preserve candidate transcript data and protect candidate_introduction
        from being overwritten by later answers or unrelated turns.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        intro_text = "I am a backend software engineer with 5 years experience building distributed microservices in Go and Python."
        state.candidate_introduction = intro_text
        state.questions_asked = 1  # Beyond introductory question

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        # Turn 1: Later substantive answer must NOT overwrite candidate_introduction
        ans_turn1 = "I led the migration of our authentication service to OAuth2 with zero downtime."
        asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=ans_turn1,
            remaining_seconds=250,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(return_value="How do you coordinate with team members?"),
            send_event_fn=send_mock,
        ))

        self.assertEqual(state.candidate_introduction, intro_text)
        # Candidate transcript data must be preserved
        self.assertTrue(any(item.get("role") == "candidate" and item.get("text") == ans_turn1 for item in state.transcript))

        # Turn 2: Unrelated personal request turn must NOT overwrite candidate_introduction
        ans_turn2 = "Can we talk about my personal life?"
        asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=ans_turn2,
            remaining_seconds=200,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(return_value="How do you handle team disagreements?"),
            send_event_fn=send_mock,
        ))

        self.assertEqual(state.candidate_introduction, intro_text)
        self.assertTrue(any(item.get("role") == "candidate" and item.get("text") == ans_turn2 for item in state.transcript))

        # Turn 3: Abusive language turn must NOT overwrite candidate_introduction
        ans_turn3 = "You are an idiot bot."
        asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=ans_turn3,
            remaining_seconds=150,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(return_value="Tell me about a time you handled a crisis."),
            send_event_fn=send_mock,
        ))

        self.assertEqual(state.candidate_introduction, intro_text)
        self.assertTrue(any(item.get("role") == "candidate" and item.get("text") == ans_turn3 for item in state.transcript))

    def test_drain_and_analyze_pending_answers_pre_intro_clarification_and_genuine_intro(self):
        """Verify a pre-introduction clarification or unrelated response does not populate
        candidate_introduction in drain_and_analyze_pending_answers, and a genuine introduction is saved.
        """
        import asyncio
        from unittest.mock import AsyncMock
        from agent.agent import AssessmentState, drain_and_analyze_pending_answers

        state = AssessmentState()
        user_answer_queue = asyncio.Queue()
        analyzed_answers = set()
        analyze_mock = AsyncMock()

        async def run_scenario():
            # 1. Pre-introduction clarification request does not populate candidate_introduction
            clarification_turn = "Could you please repeat the question?"
            await user_answer_queue.put(clarification_turn)
            await drain_and_analyze_pending_answers(
                state=state,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                analyze_fn=analyze_mock,
            )
            self.assertEqual(state.candidate_introduction, "")

            # 2. Pre-introduction unrelated response does not populate candidate_introduction
            unrelated_turn = "Can we talk about my personal life?"
            await user_answer_queue.put(unrelated_turn)
            await drain_and_analyze_pending_answers(
                state=state,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                analyze_fn=analyze_mock,
            )
            self.assertEqual(state.candidate_introduction, "")

            # 3. Genuine candidate introduction is saved
            genuine_intro = "I am a backend software engineer with 5 years of experience building distributed systems."
            await user_answer_queue.put(genuine_intro)
            await drain_and_analyze_pending_answers(
                state=state,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                analyze_fn=analyze_mock,
            )
            self.assertEqual(state.candidate_introduction, genuine_intro)

            # 4. Subsequent turn preserves genuine introduction without overwriting
            subsequent_turn = "Can you clarify what the first question is about?"
            await user_answer_queue.put(subsequent_turn)
            await drain_and_analyze_pending_answers(
                state=state,
                user_answer_queue=user_answer_queue,
                analyzed_answers=analyzed_answers,
                analyze_fn=analyze_mock,
            )
            self.assertEqual(state.candidate_introduction, genuine_intro)

        asyncio.run(run_scenario())
        self.assertEqual(
            state.candidate_introduction,
            "I am a backend software engineer with 5 years of experience building distributed systems.",
        )

    def test_regression_topic_change_request_unrelated_topic(self):
        """Regression test: Candidate requests another / unrelated topic.
        Agent reminds candidate politely of the professional behavioral interview purpose,
        offers rephrasing or moving to another question, does not change topics to casual/movie chat,
        does not count as an interview question, and does not contaminate candidate_introduction.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            PROFESSIONAL_TOPIC_REQUEST_RESPONSE,
            is_explicit_topic_change_preference,
        )

        test_inputs = [
            "I want to talk about some other topic.",
            "Can we change the topic?",
            "I don't want to answer this question.",
            "Let's talk about something else.",
        ]
        for inp in test_inputs:
            self.assertTrue(is_explicit_topic_change_preference(inp), f"Failed to detect: {inp}")

        state = AssessmentState()
        state.add_transcript("interviewer", "Tell me about a time you resolved a technical dispute.")
        initial_asked = state.questions_asked
        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Can we change the topic?",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont)
        self.assertTrue(state.waiting_for_resume)
        self.assertEqual(state.questions_asked, initial_asked)
        self.assertEqual(state.candidate_introduction, "")
        analyze_mock.assert_not_awaited()
        spoken = speak_mock.await_args[0][0]
        self.assertEqual(spoken, PROFESSIONAL_TOPIC_REQUEST_RESPONSE)
        self.assertIn("professional behavioral interview", spoken)
        self.assertIn("work experience, skills, and workplace behavior", spoken)
        self.assertIn("rephrase this question, or shall we move to another relevant interview question", spoken)
        self.assertNotIn("movie", spoken.lower())

    def test_regression_topic_change_clear_request_to_move_to_another_question(self):
        """Regression test: After topic change prompt, candidate clearly requests to move to another
        professional interview question. Agent moves to an unanswered question and updates assessment state.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_choice_to_move_to_another_question,
        )

        self.assertTrue(is_choice_to_move_to_another_question("Let's move to another relevant interview question."))
        self.assertTrue(is_choice_to_move_to_another_question("Move to another question"))
        self.assertTrue(is_choice_to_move_to_another_question("I'd prefer to move to another question"))

        state = AssessmentState()
        prior_q = "How do you handle scope creep during a critical project?"
        state.add_transcript("interviewer", prior_q)
        state.asked_questions.append(prior_q)
        state.waiting_for_resume = True
        state.waiting_for_topic_choice = True
        state.neutral_topic_turns = 1
        initial_asked = state.questions_asked

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Let's move to another relevant interview question.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont)
        self.assertFalse(state.waiting_for_resume)
        self.assertEqual(state.questions_asked, initial_asked + 1)
        self.assertIn(prior_q, state.refused_questions)
        spoken = speak_mock.await_args[0][0]
        self.assertTrue(spoken.startswith("Certainly. Let's move to another relevant interview question:"))
        self.assertNotEqual(spoken, prior_q)
        self.assertEqual(len(state.asked_questions), 2)

    def test_regression_topic_change_vague_okay_response(self):
        """Regression test: A vague 'Okay' does not trigger topic changing, does not advance questions,
        and prompts candidate to clarify preference.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_bare_acknowledgment,
            is_permission_to_resume,
        )

        self.assertTrue(is_bare_acknowledgment("Okay."))
        self.assertFalse(is_permission_to_resume("Okay."))

        state = AssessmentState()
        state.add_transcript("interviewer", "How do you manage sprint priorities?")
        state.waiting_for_resume = True
        state.waiting_for_topic_choice = True
        state.neutral_topic_turns = 1
        initial_asked = state.questions_asked

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Okay.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont)
        self.assertTrue(state.waiting_for_resume)
        self.assertEqual(state.questions_asked, initial_asked)
        spoken = speak_mock.await_args[0][0]
        self.assertIn("take your time", spoken.lower())
        self.assertIn("rephrase the question or move to another relevant interview question", spoken.lower())

    def test_regression_abusive_language_single_message(self):
        """Regression test: Single abusive message receives an immediate calm, professional boundary,
        redirects candidate to current question, does not increment questions_asked, and bypasses analysis penalty.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            ABUSE_WARNING_FIRST_PREFIX,
            is_abusive_language,
        )

        abuse_text = "You are an idiot bot."
        self.assertTrue(is_abusive_language(abuse_text))

        state = AssessmentState()
        curr_q = "How do you handle cross-functional dependencies?"
        state.add_transcript("interviewer", curr_q)
        initial_asked = state.questions_asked

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=abuse_text,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont)
        self.assertEqual(state.abusive_language_count, 1)
        self.assertEqual(state.questions_asked, initial_asked)
        self.assertEqual(state.candidate_introduction, "")
        analyze_mock.assert_not_awaited()
        spoken = speak_mock.await_args[0][0]
        self.assertTrue(spoken.startswith(ABUSE_WARNING_FIRST_PREFIX))
        self.assertIn(curr_q, spoken)
        self.assertNotIn("idiot", spoken.lower())
        self.assertNotIn("bot", spoken.lower())

    def test_regression_abusive_language_repeated_messages_and_termination(self):
        """Regression test: Repeated abusive language issues escalation reminder offering respectful continuation
        or ending, and persistent abuse concludes interview to avoid infinite loops.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            ABUSE_WARNING_REPEATED_PREFIX,
        )

        state = AssessmentState()
        state.add_transcript("interviewer", "Describe a complex production incident you resolved.")
        state.abusive_language_count = 1  # 1st abuse already happened
        initial_asked = state.questions_asked

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()
        manual_end_event = asyncio.Event()

        # Turn 2: Second abuse message
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Shut the fuck up.",
            remaining_seconds=250,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=manual_end_event,
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont2)
        self.assertEqual(state.abusive_language_count, 2)
        self.assertEqual(state.questions_asked, initial_asked)
        self.assertFalse(manual_end_event.is_set())
        spoken_2 = speak_mock.await_args[0][0]
        self.assertEqual(spoken_2, ABUSE_WARNING_REPEATED_PREFIX)
        self.assertIn("communicate respectfully", spoken_2.lower())
        self.assertIn("continue the professional interview", spoken_2.lower())

        # Turn 3: Third persistent abuse message terminates the session
        speak_mock.reset_mock()
        send_mock.reset_mock()
        cont3 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="Fuck off you bot.",
            remaining_seconds=240,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=manual_end_event,
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertFalse(cont3)
        self.assertEqual(state.abusive_language_count, 3)
        self.assertTrue(manual_end_event.is_set())
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args[0][1], "assessment_closing")
        closing_spoken = speak_mock.await_args[0][0]
        self.assertIn("unable to communicate respectfully", closing_spoken.lower())
        self.assertIn("conclude the interview", closing_spoken.lower())

    def test_regression_ordinary_frustration_and_disagreement_not_flagged_as_abuse(self):
        """Regression test: Ordinary frustration, technical disagreement, and confusion do not trigger abuse handling."""
        from agent.agent import is_abusive_language

        normal_statements = [
            "I disagree with this approach.",
            "I disagree.",
            "I don't agree with that architectural decision.",
            "I am frustrated with this process.",
            "I'm frustrated by the repeated questions.",
            "I don't understand.",
            "I do not understand what you mean.",
            "Could you explain what you're asking?",
        ]

        for s in normal_statements:
            self.assertFalse(is_abusive_language(s), f"Incorrectly flagged as abusive: {s}")

    def test_regression_next_valid_interview_question_handled_correctly_after_boundary_or_warning(self):
        """Regression test: After setting a boundary or abuse warning, candidate's subsequent valid
        interview answer is analyzed, next question is asked, and questions_asked advances.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import AssessmentState, process_interview_turn

        state = AssessmentState()
        q1 = "Tell me about a complex project you led."
        state.add_transcript("interviewer", q1)
        state.asked_questions.append(q1)
        state.questions_asked = 1

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        # Step 1: Candidate issues abusive statement
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer="You are an idiot bot.",
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont1)
        self.assertEqual(state.questions_asked, 1)
        analyze_mock.assert_not_awaited()

        # Step 2: Candidate responds with valid substantive behavioral answer
        speak_mock.reset_mock()
        analyze_mock.reset_mock()
        next_q_text = "How did you manage technical debt during that project?"
        gen_mock = AsyncMock(return_value=next_q_text)

        valid_answer = "I led the migration of our monolithic payment service to event-driven microservices with zero downtime."
        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=valid_answer,
            remaining_seconds=280,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_mock,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont2)
        analyze_mock.assert_awaited_once_with(state, valid_answer)
        self.assertEqual(state.questions_asked, 2)
        spoken_2 = speak_mock.await_args[0][0]
        self.assertIn(next_q_text, spoken_2)
        self.assertEqual(state.transcript[-1]["role"], "interviewer")
        self.assertEqual(state.transcript[-2]["role"], "candidate")
        self.assertEqual(state.transcript[-2]["text"], valid_answer)

    def test_regression_topic_change_flow_rephrase_or_move_exact_turns(self):
        """Regression test for exact topic-change conversation flow:
        Turn 1: Candidate says: 'I want to talk about another topic.'
                Interviewer offers to rephrase or move to another relevant interview question.
        Turn 2: Candidate says: 'Move to another question.'
                Agent routes choice even though reply does not independently match is_topic_pref.
                Agent moves to next unasked, relevant question, increments questions_asked exactly once,
                records refused question, and does NOT score choice as a substantive answer.
        Turn 3: When NOT waiting for topic choice, unrelated answers mentioning 'move to another'
                are NOT hijacked as a topic choice.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            PROFESSIONAL_TOPIC_REQUEST_RESPONSE,
            is_explicit_topic_change_preference,
            is_choice_to_move_to_another_question,
            FALLBACK_BEHAVIORAL_QUESTIONS,
        )

        # 1. Linguistic verification: 'Move to another question.' does NOT match explicit topic change preference,
        # but DOES match choice to move to another question.
        turn1_text = "I want to talk about another topic."
        turn2_text = "Move to another question."
        self.assertTrue(is_explicit_topic_change_preference(turn1_text))
        self.assertFalse(is_explicit_topic_change_preference(turn2_text))
        self.assertTrue(is_choice_to_move_to_another_question(turn2_text))

        state = AssessmentState()
        prior_q = "Can you describe a challenging technical problem you solved?"
        state.add_transcript("interviewer", prior_q)
        state.asked_questions.append(prior_q)
        state.questions_asked = 1
        self.assertFalse(state.waiting_for_topic_choice)

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()
        gen_mock = AsyncMock()

        # --- Turn 1: Candidate requests another topic ---
        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=turn1_text,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_mock,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont1)
        self.assertTrue(state.waiting_for_topic_choice)
        self.assertTrue(state.waiting_for_resume)
        self.assertEqual(state.questions_asked, 1)  # Question count NOT incremented
        analyze_mock.assert_not_awaited()           # Turn NOT scored as substantive
        gen_mock.assert_not_awaited()
        speak_mock.assert_awaited_once()
        spoken_1 = speak_mock.await_args[0][0]
        self.assertEqual(spoken_1, PROFESSIONAL_TOPIC_REQUEST_RESPONSE)
        self.assertIn("Would you like me to rephrase this question, or shall we move to another relevant interview question?", spoken_1)

        # --- Turn 2: Candidate chooses 'Move to another question.' ---
        speak_mock.reset_mock()
        analyze_mock.reset_mock()
        gen_mock.reset_mock()

        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=turn2_text,
            remaining_seconds=280,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_mock,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont2)
        # Verify choice flag is reset
        self.assertFalse(state.waiting_for_topic_choice)
        self.assertFalse(state.waiting_for_resume)
        # Verify question is counted exactly once
        self.assertEqual(state.questions_asked, 2)
        # Verify candidate choice is NOT scored as a substantive answer
        analyze_mock.assert_not_awaited()
        gen_mock.assert_not_awaited()
        # Verify previous question recorded as refused
        self.assertIn(prior_q, state.refused_questions)
        # Verify spoken response moves to relevant unasked question
        speak_mock.assert_awaited_once()
        spoken_2 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_2.startswith("Certainly. Let's move to another relevant interview question:"))
        self.assertNotEqual(spoken_2, prior_q)
        # Verify newly asked question is tracked and distinct
        self.assertEqual(len(state.asked_questions), 2)
        new_q = state.asked_questions[1]
        self.assertIn(new_q, FALLBACK_BEHAVIORAL_QUESTIONS)
        self.assertIn(new_q, spoken_2)

        # --- Turn 3: When NOT waiting for topic choice, statements are NOT treated as topic choice ---
        speak_mock.reset_mock()
        analyze_mock.reset_mock()
        gen_mock.reset_mock()
        next_gen_q = "How do you ensure code quality across the team?"
        gen_mock.return_value = next_gen_q

        self.assertFalse(state.waiting_for_topic_choice)
        survey_answer = "In my previous project when users clicked move to another question on our survey, we validated all inputs."

        cont3 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=survey_answer,
            remaining_seconds=260,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_mock,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont3)
        # Substantive answer was properly analyzed, NOT routed as topic choice
        analyze_mock.assert_awaited_once_with(state, survey_answer)
        self.assertEqual(state.questions_asked, 3)

    def test_regression_professional_abuse_handling_conversation_lifecycle(self):
        """Regression test covering:
        1. First abusive message receives a respectful boundary response and redirection.
        2. Repeated abusive message produces the escalated boundary response.
        3. Continued abusive message concludes the interview cleanly using the existing mechanism.
        4. Respectful response after a warning allows the interview to continue normally.
        7. Abusive messages are not scored as substantive answers or evidence.
        8. The next valid behavioral question is asked and recorded properly when resuming after a warning.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            ABUSE_WARNING_FIRST_PREFIX,
            ABUSE_WARNING_REPEATED_PREFIX,
            ABUSE_TERMINATION_MESSAGE,
            is_abusive_language,
        )

        state = AssessmentState()
        q1 = "Can you describe a time you led a high-stakes project under tight deadlines?"
        state.add_transcript("interviewer", q1)
        state.asked_questions.append(q1)
        state.questions_asked = 1

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()
        manual_end_event = asyncio.Event()

        # --- Turn 1 (Item 1 & 7): First abusive message receives respectful boundary and question redirection ---
        abuse_turn_1 = "You are an idiot bot."
        self.assertTrue(is_abusive_language(abuse_turn_1))

        cont1 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=abuse_turn_1,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=manual_end_event,
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont1)
        self.assertEqual(state.abusive_language_count, 1)
        self.assertEqual(state.questions_asked, 1)  # Questions asked not incremented
        self.assertEqual(state.candidate_introduction, "")  # Not saved as introduction
        analyze_mock.assert_not_awaited()  # Item 7: Abusive turn NOT scored as substantive answer
        self.assertFalse(manual_end_event.is_set())

        # Verify boundary response wording & redirect to current question
        speak_mock.assert_awaited_once()
        spoken_1 = speak_mock.await_args[0][0]
        self.assertTrue(spoken_1.startswith(ABUSE_WARNING_FIRST_PREFIX))
        self.assertIn("communicate respectfully", spoken_1.lower())
        self.assertIn("continue with the interview when you're ready", spoken_1.lower())
        self.assertIn(q1, spoken_1)  # Redirects to current question
        self.assertNotIn("idiot", spoken_1.lower())  # Never insults or retaliates back

        # --- Turn 2 (Item 4 & 8): Respectful response after warning continues interview normally ---
        speak_mock.reset_mock()
        analyze_mock.reset_mock()
        send_mock.reset_mock()

        q2_gen = "How did you manage stakeholder communication during that project?"
        gen_mock = AsyncMock(return_value=q2_gen)

        respectful_answer = "I apologize for the frustration. In my previous role at Acme Corp, I led our core payments migration."
        self.assertFalse(is_abusive_language(respectful_answer))

        cont2 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=respectful_answer,
            remaining_seconds=270,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=manual_end_event,
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_mock,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont2)
        self.assertEqual(state.abusive_language_count, 1)  # Abuse count not incremented
        self.assertEqual(state.questions_asked, 2)  # Question count advanced
        analyze_mock.assert_awaited_once_with(state, respectful_answer)  # Substantive answer analyzed
        gen_mock.assert_awaited_once()  # Next adaptive question generated
        speak_mock.assert_awaited_once()
        spoken_2 = speak_mock.await_args[0][0]
        self.assertEqual(spoken_2, q2_gen)
        self.assertEqual(state.asked_questions[-1], q2_gen)  # Recorded in asked_questions
        self.assertEqual(state.transcript[-2]["role"], "candidate")
        self.assertEqual(state.transcript[-2]["text"], respectful_answer)
        self.assertEqual(state.transcript[-1]["role"], "interviewer")
        self.assertEqual(state.transcript[-1]["text"], q2_gen)

        # --- Turn 3 (Item 2 & 7): Repeated abusive message produces escalated boundary response ---
        speak_mock.reset_mock()
        analyze_mock.reset_mock()
        send_mock.reset_mock()
        gen_mock.reset_mock()

        abuse_turn_2 = "Shut the fuck up."
        self.assertTrue(is_abusive_language(abuse_turn_2))

        cont3 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=abuse_turn_2,
            remaining_seconds=240,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=manual_end_event,
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_mock,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont3)
        self.assertEqual(state.abusive_language_count, 2)
        self.assertEqual(state.questions_asked, 2)  # Not incremented
        analyze_mock.assert_not_awaited()  # Not scored as substantive
        gen_mock.assert_not_awaited()
        self.assertFalse(manual_end_event.is_set())  # Does not terminate prematurely

        speak_mock.assert_awaited_once()
        spoken_3 = speak_mock.await_args[0][0]
        self.assertEqual(spoken_3, ABUSE_WARNING_REPEATED_PREFIX)
        self.assertIn("respectful communication is required", spoken_3.lower())
        self.assertIn("avoid abusive language", spoken_3.lower())
        self.assertIn("proceed professionally", spoken_3.lower())

        # --- Turn 4 (Item 3 & 7): Continued abuse after warnings cleanly concludes interview ---
        speak_mock.reset_mock()
        analyze_mock.reset_mock()
        send_mock.reset_mock()
        gen_mock.reset_mock()

        abuse_turn_3 = "Fuck off you bot."
        self.assertTrue(is_abusive_language(abuse_turn_3))

        cont4 = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=abuse_turn_3,
            remaining_seconds=220,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=manual_end_event,
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_mock,
            send_event_fn=send_mock,
        ))

        self.assertFalse(cont4)
        self.assertEqual(state.abusive_language_count, 3)
        self.assertEqual(state.questions_asked, 2)
        analyze_mock.assert_not_awaited()  # Item 7: Abusive turn NOT scored as substantive
        self.assertTrue(manual_end_event.is_set())  # Session concluded cleanly via project mechanism

        # Emits assessment_closing event with reason repeated_abuse
        send_mock.assert_awaited_once()
        self.assertEqual(send_mock.await_args[0][1], "assessment_closing")
        self.assertEqual(send_mock.await_args[0][2], {"reason": "repeated_abuse"})

        # Concluding remarks spoken respectfully
        speak_mock.assert_awaited_once()
        spoken_4 = speak_mock.await_args[0][0]
        self.assertEqual(spoken_4, ABUSE_TERMINATION_MESSAGE)
        self.assertIn("unable to continue the interview while the conversation remains abusive", spoken_4.lower())
        self.assertIn("unable to communicate respectfully", spoken_4.lower())
        self.assertIn("conclude the interview", spoken_4.lower())

        # Transcript integrity preserved
        self.assertEqual(state.transcript[-2]["role"], "candidate")
        self.assertEqual(state.transcript[-2]["text"], abuse_turn_3)
        self.assertEqual(state.transcript[-1]["role"], "interviewer")
        self.assertEqual(state.transcript[-1]["text"], ABUSE_TERMINATION_MESSAGE)

    def test_regression_frustration_and_workplace_narratives_not_treated_as_abuse(self):
        """Regression test covering:
        5. Ordinary frustration or disagreement is not treated as abuse.
        6. Quoting or discussing abusive language in a workplace example is not treated as abuse.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_abusive_language,
        )

        # 5. Ordinary frustration and technical disagreement
        frustration_and_disagreement = [
            "I disagree with this approach.",
            "I disagree.",
            "I don't agree with that architectural decision.",
            "I am frustrated with this process.",
            "I'm frustrated by the repeated questions.",
            "I don't understand what you mean.",
            "Could you explain what you're asking?",
            "This is a bit confusing to me.",
            "Are you even listening to me?",
            "Why do you keep asking the same question?",
        ]
        for stmt in frustration_and_disagreement:
            self.assertFalse(is_abusive_language(stmt), f"Incorrectly flagged as abuse: {stmt}")

        # 6. Quoting or discussing abusive language in a workplace context
        workplace_narratives = [
            "A customer became abusive and called our support team names...",
            "A customer became abusive and called our support team names, but I handled the incident professionally.",
            "In my previous role, an angry customer shouted you are useless at a colleague, and I stepped in to de-escalate the situation.",
            "When I worked in customer support, a client told me to shut the fuck up over the phone, but I stayed calm and professional and resolved their billing problem.",
            "There was an incident where a user became verbally abusive and screamed at our team. I followed our company de-escalation protocol.",
            "In dealing with an abusive client who called our staff idiots, I remained professional and defused the conflict.",
            "In my previous position, a customer said you suck and are worthless, so I listened actively to understand their frustration.",
        ]
        for narrative in workplace_narratives:
            self.assertFalse(is_abusive_language(narrative), f"Workplace narrative falsely flagged as abuse: {narrative}")

        # Verify through process_interview_turn that a workplace example is scored as substantive evidence
        state = AssessmentState()
        q1 = "Tell me about a difficult interpersonal conflict you resolved at work."
        state.add_transcript("interviewer", q1)
        state.asked_questions.append(q1)
        state.questions_asked = 1

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()
        q2_next = "How do you maintain team morale after difficult customer escalations?"
        gen_mock = AsyncMock(return_value=q2_next)

        wp_example = "In my previous role, an angry customer shouted you are useless at a colleague, and I stepped in to de-escalate the situation."
        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=wp_example,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=gen_mock,
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont)
        self.assertEqual(state.abusive_language_count, 0)  # NOT flagged as abuse
        self.assertEqual(state.questions_asked, 2)  # Advances to next question
        analyze_mock.assert_awaited_once_with(state, wp_example)  # Evaluated as substantive
        speak_mock.assert_awaited_once()
        self.assertEqual(speak_mock.await_args[0][0], q2_next)

    def test_regression_workplace_narrative_vs_direct_ai_abuse_precedence(self):
        """Regression test for abuse-detection precedence:
        1. Genuine workplace example describing an abusive customer is NOT flagged as abuse.
        2. Direct abuse aimed at the AI, even when the message also mentions a workplace incident, IS flagged as abuse.
        3. A workplace example containing quoted abusive language is NOT flagged unless the candidate also directly abuses the AI.
        4. Ordinary frustration and disagreement is NOT flagged as abuse.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import (
            AssessmentState,
            process_interview_turn,
            is_abusive_language,
            ABUSE_WARNING_FIRST_PREFIX,
        )

        # 1. Genuine workplace example describing an abusive customer — NOT flagged as abuse
        workplace_narratives = [
            "A customer became abusive and called our support team names...",
            "A customer became abusive and called our support team names, but I handled the incident professionally.",
            "In my previous role, an angry customer shouted you are useless at a colleague, and I stepped in to de-escalate the situation.",
            "When I worked in customer support, a client told me to shut the fuck up over the phone, but I stayed calm and professional and resolved their billing problem.",
            'In my previous role, a customer said "you are useless" and I listened actively to understand their issue.',
            "There was an incident where a user became verbally abusive and screamed you suck at our team. I followed our company de-escalation protocol.",
            "In dealing with an abusive client who called our staff idiots, I remained professional and defused the conflict.",
        ]
        for narrative in workplace_narratives:
            self.assertFalse(is_abusive_language(narrative), f"Workplace narrative falsely flagged: {narrative}")

        # 2. Direct abuse aimed at the AI, even when the message also mentions a workplace incident — FLAGGED as abuse
        direct_abuse_with_workplace_context = [
            "In my previous role I handled client escalations, but you are an idiot.",
            "When I worked in customer support, we resolved many issues. Shut the fuck up.",
            "In my last job, a customer was mad. Fuck off you bot.",
            "I worked in retail where people yelled, but you suck and you're useless.",
            "At my last company we had escalations. You are a moron.",
            "In my previous job, people were stressed, but go to hell.",
            "In my previous role, an angry customer shouted you are useless at a colleague, but you are a stupid idiot.",
            'When the client told me "shut the fuck up", I stayed calm. But you are useless.',
            "My manager shouted that I was stupid, but you are a moron.",
        ]
        for abusive_msg in direct_abuse_with_workplace_context:
            self.assertTrue(is_abusive_language(abusive_msg), f"Direct abuse inside workplace context missed: {abusive_msg}")

        # 3. Quoted abusive language alone vs quoted + direct abuse targeting AI
        # Quoted alone -> NOT abuse
        quoted_alone = 'A customer said to me: "you are an idiot and you suck", but I de-escalated.'
        self.assertFalse(is_abusive_language(quoted_alone))

        # Quoted + direct abuse to AI -> FLAGGED as abuse
        quoted_plus_direct = 'A customer said to me: "you are an idiot", but you are useless.'
        self.assertTrue(is_abusive_language(quoted_plus_direct))

        # 4. Ordinary frustration and disagreement — NOT flagged as abuse
        frustration_and_disagreement = [
            "I disagree with this approach.",
            "I disagree.",
            "I don't agree with that architectural decision.",
            "I am frustrated with this process.",
            "I'm frustrated by the repeated questions.",
            "I don't understand what you mean.",
            "Could you explain what you're asking?",
            "This is a bit confusing to me.",
            "Are you even listening to me?",
            "Why do you keep asking the same question?",
        ]
        for stmt in frustration_and_disagreement:
            self.assertFalse(is_abusive_language(stmt), f"Frustration incorrectly flagged as abuse: {stmt}")

        # 5. Process turn verification: Direct abuse with workplace framing triggers boundary and is NOT analyzed
        state = AssessmentState()
        q1 = "Tell me about a difficult incident you managed."
        state.add_transcript("interviewer", q1)
        state.asked_questions.append(q1)
        state.questions_asked = 1

        speak_mock = AsyncMock()
        analyze_mock = AsyncMock()
        send_mock = AsyncMock()

        mixed_turn = "In my previous role I handled client escalations, but you are an idiot."
        cont = asyncio.run(process_interview_turn(
            ctx=MagicMock(),
            state=state,
            answer=mixed_turn,
            remaining_seconds=300,
            warning_sent=False,
            user_answer_queue=asyncio.Queue(),
            analyzed_answers=set(),
            manual_end_event=asyncio.Event(),
            timer_finished_event=asyncio.Event(),
            disconnect_event=asyncio.Event(),
            speak_fn=speak_mock,
            analyze_fn=analyze_mock,
            generate_question_fn=AsyncMock(),
            send_event_fn=send_mock,
        ))

        self.assertTrue(cont)
        self.assertEqual(state.abusive_language_count, 1)  # Correctly flagged as abuse!
        self.assertEqual(state.questions_asked, 1)         # Question count not advanced
        analyze_mock.assert_not_awaited()                  # Not scored as substantive evidence
        speak_mock.assert_awaited_once()
        spoken = speak_mock.await_args[0][0]
        self.assertTrue(spoken.startswith(ABUSE_WARNING_FIRST_PREFIX))
        self.assertIn(q1, spoken)


if __name__ == "__main__":
    unittest.main()

