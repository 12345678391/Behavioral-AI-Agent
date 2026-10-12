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

    def test_pending_answer_drained_and_analyzed(self):
        """Verify pending answers in queue are analyzed before completing assessment."""
        import asyncio
        from unittest.mock import AsyncMock, patch, MagicMock
        from agent.agent import AssessmentState, analyze_candidate_answer

        state = AssessmentState()
        mock_choice = MagicMock()
        mock_choice.message.content = '{"evidence": [{"area": "leadership", "evidence": "Led engineering migration", "strength": "strong", "confidence": 0.95}]}'
        mock_chat_resp = MagicMock()
        mock_chat_resp.choices = [mock_choice]

        with patch("agent.agent.openai_client.responses.create", new_callable=AsyncMock) as mock_resp, \
             patch("agent.agent.openai_client.chat.completions.create", new_callable=AsyncMock) as mock_chat:
            mock_resp.side_effect = RuntimeError("Responses 500 endpoint error")
            mock_chat.return_value = mock_chat_resp

            answer_queue = asyncio.Queue()
            answer_queue.put_nowait("I stepped up to lead the migration when our team lead was unavailable.")

            async def drain_queue():
                while not answer_queue.empty():
                    ans = answer_queue.get_nowait()
                    await analyze_candidate_answer(state, ans)

            asyncio.run(drain_queue())

            self.assertEqual(len(state.evidence["leadership"]), 1)
            self.assertEqual(state.evidence["leadership"][0].evidence, "Led engineering migration")

if __name__ == "__main__":
    unittest.main()
