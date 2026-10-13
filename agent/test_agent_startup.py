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
            mock_load.assert_called_once_with(sample_rate=16000)
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


if __name__ == "__main__":
    unittest.main()

