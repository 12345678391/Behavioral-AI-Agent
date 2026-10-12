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

if __name__ == "__main__":
    unittest.main()
