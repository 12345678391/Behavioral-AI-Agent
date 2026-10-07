import { useEffect, useState } from "react";
import { getToken } from "./api";
import { joinRoom } from "./LiveKitRoom";
import {
  INTERVIEW_DURATION_SECONDS,
  WARNING_SECONDS,
  BEHAVIORAL_AREAS,
} from "./config";



function App() {
  const [room, setRoom] = useState(null);

  const [connected, setConnected] =
    useState(false);

  const [timeLeft, setTimeLeft] =
    useState(INTERVIEW_DURATION_SECONDS);

  const [currentQuestion, setCurrentQuestion] =
    useState(
      "The AI interviewer will introduce the assessment."
    );

  const [conversation, setConversation] =
    useState([]);

  const [assessmentStarted, setAssessmentStarted] =
    useState(false);

  const [assessmentFinished, setAssessmentFinished] =
    useState(false);

  const [assessmentResult, setAssessmentResult] =
    useState(null);

  const [ending, setEnding] =
    useState(false);

  const [errorMessage, setErrorMessage] =
    useState("");

  // ==========================================================
  // FORMAT TIME
  // ==========================================================

  function formatTime(seconds) {
    const minutes = Math.floor(seconds / 60);

    const remainingSeconds =
      seconds % 60;

    return `${String(minutes).padStart(
      2,
      "0"
    )}:${String(remainingSeconds).padStart(
      2,
      "0"
    )}`;
  }

  // ==========================================================
  // LIVEKIT EVENTS
  // ==========================================================

  function handleData(data) {
    console.log(
      "Assessment event:",
      data
    );

    const type = data?.type;
    const eventData = data?.data || {};

    // --------------------------------------------------------
    // ASSESSMENT STARTED
    // --------------------------------------------------------

    if (type === "assessment_started") {
      setAssessmentStarted(true);

      if (
        eventData.duration_seconds
      ) {
        setTimeLeft(
          eventData.duration_seconds
        );
      }

      return;
    }

    // --------------------------------------------------------
    // NEW INTERVIEW QUESTION / SCENARIO
    // --------------------------------------------------------

    if (
      type === "interview_question"
    ) {
      const question =
        eventData.question || "";

      if (question) {
        setCurrentQuestion(question);

        setConversation(
          (previous) => [
            ...previous,
            {
              id: crypto.randomUUID(),
              role: "ai",
              text: question,
            },
          ]
        );
      }

      return;
    }

    // --------------------------------------------------------
    // ASSESSMENT WARNING
    // --------------------------------------------------------

    if (
      type === "assessment_warning"
    ) {
      const seconds =
        eventData.seconds_remaining ||
        40;

      setTimeLeft(seconds);

      setConversation(
        (previous) => [
          ...previous,
          {
           
            id: crypto.randomUUID(),
            role: "ai",
            text:
               "Thank you for sharing your responses. We have about 40 seconds remaining in the assessment. Please finish your current response. I won't start another question. Once you're finished, we'll conclude the interview. Thank you for your time.",

          },
        ]
      );

      return;
    }

    // --------------------------------------------------------
    // CLOSING
    // --------------------------------------------------------

    if (
      type === "assessment_closing"
    ) {
      setConversation(
        (previous) => [
          ...previous,
          {
            id: crypto.randomUUID(),
            role: "ai",
            text:
              "Thank you for completing the behavioral assessment. I appreciate your time and your responses. The interview is now complete. Thank you.",
          },
        ]
      );

      return;
    }

    // --------------------------------------------------------
    // ASSESSMENT ERROR
    // --------------------------------------------------------

    if (
      type === "assessment_error"
    ) {
      console.error(
        "ASSESSMENT ERROR:",
        eventData
      );

      setErrorMessage(
        eventData.message ||
          "The assessment finished, but the final behavioral analysis could not be generated."
      );

      setEnding(false);
      setConnected(false);
      setAssessmentStarted(false);

      return;
    }

    // --------------------------------------------------------
    // FINAL RESULT
    // --------------------------------------------------------

    if (
      type === "final_result"
    ) {
      console.log(
        "FINAL ASSESSMENT RESULT:",
        data
      );

      setAssessmentResult(
        data.data || data
      );

      setAssessmentFinished(true);
      setConnected(false);
      setEnding(false);

      return;
    }
  }

  // ==========================================================
  // START ASSESSMENT
  // ==========================================================

  async function startAssessment() {
    try {
      setErrorMessage("");

      setAssessmentFinished(false);
      setAssessmentResult(null);

      setConversation([]);

      setCurrentQuestion(
        "Connecting to the Behavioral AI interviewer..."
      );

      setTimeLeft(
        INTERVIEW_DURATION_SECONDS
      );

      const data =
        await getToken(
          "candidate"
        );

      const newRoom =
        await joinRoom(
          data.token,
          data.serverUrl,
          handleData,
          () => {
            console.log(
              "Assessment room connected."
            );

            setConnected(true);
          },
          () => {
            console.log(
              "Assessment room disconnected."
            );
          }
        );

      setRoom(newRoom);

      setConnected(true);

      setAssessmentStarted(true);

    } catch (error) {
      console.error(
        "Assessment start error:",
        error
      );

      setErrorMessage(
        "Could not start the assessment. Make sure the Node.js server and Python AI agent are running."
      );
    }
  }

  // ==========================================================
  // END ASSESSMENT
  // ==========================================================

  async function endAssessment() {
    console.log("END CLICKED — room:", room);
    if (!room || ending) {
      return;
    }

    try {
      setEnding(true);

      console.log(
        "Requesting assessment completion..."
      );

      const payload =
        new TextEncoder().encode(
          JSON.stringify({
            type: "end_assessment",
          })
        );

      await room.localParticipant.publishData(
        payload,
        {
          reliable: true,
          topic: "behavioral-ui",
        }
      );

      /*
       * IMPORTANT:
       *
       * Do NOT disconnect here.
       *
       * Python needs the LiveKit connection
       * to speak the closing message and send
       * final_result.
       */

    } catch (error) {
      console.error(
        "Could not end assessment:",
        error
      );

      setEnding(false);

      setErrorMessage(
        "Could not complete the assessment."
      );
    }
  }

  // ==========================================================
  // FRONTEND TIMER
  // ==========================================================

  useEffect(() => {
    if (!connected) {
      return;
    }

    if (assessmentFinished) {
      return;
    }

    const timer =
      setInterval(() => {
        setTimeLeft(
          (previous) => {
            if (previous <= 1) {
              clearInterval(timer);
              return 0;
            }

            return previous - 1;
          }
        );
      }, 1000);

    return () => {
      clearInterval(timer);
    };
  }, [
    connected,
    assessmentFinished,
  ]);

  // ==========================================================
  // TIMER ZERO
  // ==========================================================

  useEffect(() => {
    if (
      !connected ||
      assessmentFinished
    ) {
      return;
    }

    if (timeLeft === 0) {
      endAssessment();
    }
  }, [
    timeLeft,
    connected,
    assessmentFinished,
  ]);

  // ==========================================================
  // SCORE
  // ==========================================================

  function getScore(
    area
  ) {
    const scores =
      assessmentResult?.final_analysis
        ?.scores;

    if (!scores) {
      return 0;
    }

    return Number(
      scores[area] || 0
    );
  }

  // ==========================================================
  // FINAL ANALYSIS
  // ==========================================================

  function getAnalysis() {
    return (
      assessmentResult?.final_analysis ||
      assessmentResult?.finalAnalysis ||
      null
    );
  }

  // ==========================================================
  // RESET
  // ==========================================================

  function startNewAssessment() {
    window.location.reload();
  }

  // ==========================================================
  // FINAL RESULT PAGE
  // ==========================================================

  if (
    assessmentFinished &&
    assessmentResult
  ) {
    const analysis =
      getAnalysis();

    const teamStyle =
      analysis?.team_working_style || {};

    return (
      <div className="min-h-screen bg-slate-950 text-white">

        <header className="border-b border-slate-800">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">

            <div>
              <h1 className="text-2xl font-bold">
                Behavioral AI
              </h1>

              <p className="mt-1 text-sm text-slate-400">
                Behavioral Assessment Report
              </p>
            </div>

            <div className="rounded-full bg-green-500/10 px-4 py-2 text-sm text-green-400">
              Assessment Complete
            </div>

          </div>
        </header>

        <main className="mx-auto max-w-6xl px-6 py-10">

          <div className="mb-10">
            <p className="text-sm uppercase tracking-widest text-indigo-400">
              Final Assessment
            </p>

            <h2 className="mt-2 text-4xl font-bold">
              Behavioral AI Assessment
            </h2>

            <p className="mt-3 max-w-3xl text-slate-400">
              A concise summary of the behavioral
              evidence observed during the assessment.
            </p>
          </div>

          {/* OVERALL SCORE */}

          <section className="rounded-3xl border border-slate-800 bg-slate-900 p-8">

            <div className="flex flex-col justify-between gap-6 md:flex-row md:items-center">

              <div>
                <p className="text-sm text-slate-400">
                  Overall Behavioral Score
                </p>

                <p className="mt-2 text-6xl font-bold">
                  {analysis?.overall_score || 0}
                  <span className="text-2xl text-slate-500">
                    /100
                  </span>
                </p>
              </div>

              <div className="w-full max-w-md">

                <div className="mb-2 flex justify-between text-sm">
                  <span className="text-slate-400">
                    Overall assessment
                  </span>

                  <span>
                    {analysis?.overall_score || 0}%
                  </span>
                </div>

                <div className="h-3 overflow-hidden rounded-full bg-slate-800">

                  <div
                    className="h-full rounded-full bg-indigo-500"
                    style={{
                      width: `${analysis?.overall_score || 0}%`,
                    }}
                  />

                </div>

              </div>

            </div>

          </section>

          {/* SCORES */}

          <section className="mt-8 rounded-3xl border border-slate-800 bg-slate-900 p-8">

            <h3 className="text-2xl font-bold">
              Behavioral Score Overview
            </h3>

            <div className="mt-6 space-y-5">

              {BEHAVIORAL_AREAS.map(
                (area) => {

                  const key =
                    area
                      .toLowerCase()
                      .replaceAll(
                        " ",
                        "_"
                      );

                  const score =
                    getScore(key);

                  return (
                    <div key={area}>

                      <div className="mb-2 flex justify-between">

                        <span className="text-slate-300">
                          {area}
                        </span>

                        <span className="font-semibold">
                          {score}%
                        </span>

                      </div>

                      <div className="h-3 rounded-full bg-slate-800">

                        <div
                          className="h-full rounded-full bg-indigo-500 transition-all"
                          style={{
                            width: `${score}%`,
                          }}
                        />

                      </div>

                    </div>
                  );
                }
              )}

            </div>

          </section>

          {/* SUMMARY */}

          <section className="mt-8 grid gap-6 md:grid-cols-2">

            <div className="rounded-3xl border border-slate-800 bg-slate-900 p-7">

              <h3 className="text-xl font-bold">
                Overall Communication
              </h3>

              <p className="mt-4 leading-relaxed text-slate-400">
                {analysis?.overall_communication ||
                  "No communication summary was generated."}
              </p>

            </div>

            <div className="rounded-3xl border border-slate-800 bg-slate-900 p-7">

              <h3 className="text-xl font-bold">
                Overall Strengths
              </h3>

              <p className="mt-4 leading-relaxed text-slate-400">
                {(analysis?.strengths || []).join(
                  " "
                ) || "No strengths summary was generated."}
              </p>

            </div>

            <div className="rounded-3xl border border-slate-800 bg-slate-900 p-7">

              <h3 className="text-xl font-bold">
                Areas for Improvement
              </h3>

              <p className="mt-4 leading-relaxed text-slate-400">
                {(analysis?.areas_for_improvement || []).join(
                  " "
                ) ||
                  "No improvement areas were identified."}
              </p>

            </div>

            <div className="rounded-3xl border border-slate-800 bg-slate-900 p-7">

              <h3 className="text-xl font-bold">
                Behavioral Summary
              </h3>

              <p className="mt-4 leading-relaxed text-slate-400">
                {analysis?.behavioral_summary ||
                  "No behavioral summary was generated."}
              </p>

            </div>

          </section>

          {/* TEAM WORKING STYLE */}

          <section className="mt-8 rounded-3xl border border-slate-800 bg-slate-900 p-8">

            <h3 className="text-2xl font-bold">
              Team Working Style
            </h3>

            <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">

              {[
                ["Collaboration", "collaboration"],
                ["Independence", "independence"],
                ["Adaptability", "adaptability"],
                ["Leadership", "leadership"],
                ["Structure", "structure"],
                ["Autonomy", "autonomy"],
              ].map(
                ([label, key]) => (

                  <div
                    key={key}
                    className="rounded-2xl border border-slate-800 bg-slate-950 p-5"
                  >

                    <div className="flex justify-between">

                      <span className="text-slate-400">
                        {label}
                      </span>

                      <span className="font-semibold">
                        {teamStyle[key] || 0}%
                      </span>

                    </div>

                    <div className="mt-3 h-2 rounded-full bg-slate-800">

                      <div
                        className="h-full rounded-full bg-cyan-500"
                        style={{
                          width: `${teamStyle[key] || 0}%`,
                        }}
                      />

                    </div>

                  </div>

                )
              )}

            </div>

            <div className="mt-8">

              <h4 className="text-lg font-semibold">
                Observed Working Style
              </h4>

              <p className="mt-3 leading-relaxed text-slate-400">
                {teamStyle.summary ||
                  "No working style summary was generated."}
              </p>

            </div>

            <div className="mt-6">

              <h4 className="text-lg font-semibold">
                Potential Team Environment Alignment
              </h4>

              <p className="mt-3 leading-relaxed text-slate-400">
                {(teamStyle.environment_alignment || []).join(
                  " • "
                ) ||
                  "No environment alignment summary was generated."}
              </p>

            </div>

          </section>

          <div className="mt-10 flex justify-center">

            <button
              onClick={startNewAssessment}
              className="rounded-xl bg-indigo-500 px-8 py-4 font-semibold transition hover:bg-indigo-600"
            >
              Start New Assessment
            </button>

          </div>

        </main>
      </div>
    );
  }

  // ==========================================================
  // MAIN INTERVIEW PAGE
  // ==========================================================

  return (
    <div className="min-h-screen bg-slate-950 text-white">

      <header className="border-b border-slate-800">

        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">

          <div>
            <h1 className="text-2xl font-bold">
              Behavioral AI
            </h1>

            <p className="mt-1 text-sm text-slate-400">
              AI-Powered Behavioral Assessment
            </p>
          </div>

          <div
            className={`rounded-full px-4 py-2 text-sm ${
              connected
                ? "bg-green-500/10 text-green-400"
                : "bg-slate-800 text-slate-400"
            }`}
          >
            {connected
              ? "Assessment Active"
              : "Ready"}
          </div>

        </div>

      </header>

      <main className="mx-auto max-w-7xl px-6 py-10">

        {errorMessage && (
          <div className="mb-6 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-red-300">
            {errorMessage}
          </div>
        )}

        {!connected && !assessmentStarted && (
          <section className="mx-auto max-w-3xl rounded-3xl border border-slate-800 bg-slate-900 p-10 text-center">

            <div className="mx-auto flex h-28 w-28 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-cyan-400">

              <span className="text-2xl font-bold">
                AI
              </span>

            </div>

            <h2 className="mt-8 text-4xl font-bold">
              Behavioral AI Assessment
            </h2>

            <p className="mx-auto mt-4 max-w-xl text-slate-400">
              You will have a natural voice conversation
              with an AI interviewer. The AI will dynamically
              generate realistic behavioral scenarios based
              on your responses.
            </p>

            <button
              onClick={startAssessment}
              className="mt-8 rounded-xl bg-indigo-500 px-8 py-4 font-semibold hover:bg-indigo-600"
            >
              Start Assessment
            </button>

          </section>
        )}

        {connected && (
          <div className="grid gap-8 lg:grid-cols-3">

            {/* MAIN */}

            <section className="lg:col-span-2">

              <div className="rounded-3xl border border-slate-800 bg-slate-900 p-8">

                <div className="flex items-center justify-between">

                  <div>

                    <p className="text-sm uppercase tracking-widest text-indigo-400">
                      Live Behavioral Interview
                    </p>

                    <h2 className="mt-2 text-2xl font-bold">
                      Adaptive Scenario
                    </h2>

                  </div>

                  <div className="text-right">

                    <p className="text-xs text-slate-500">
                      Time Remaining
                    </p>

                    <p
                      className={`text-3xl font-bold ${
                        timeLeft <= WARNING_SECONDS
                        
                          ? "text-red-400"
                          : "text-white"
                      }`}
                    >
                      {formatTime(timeLeft)}
                    </p>

                  </div>

                </div>

                {/* CURRENT QUESTION */}

                <div className="mt-8 rounded-2xl border border-indigo-500/20 bg-indigo-500/5 p-7">

                  <p className="text-xs uppercase tracking-widest text-indigo-400">
                    AI Interviewer
                  </p>

                  <p className="mt-4 text-xl leading-relaxed text-slate-100">
                    {currentQuestion}
                  </p>

                </div>

                {/* CONVERSATION */}

                <div className="mt-8">

                  <h3 className="text-lg font-semibold">
                    Conversation
                  </h3>

                  <div className="mt-4 max-h-96 space-y-4 overflow-y-auto">

                    {conversation.length === 0 && (
                      <p className="text-sm text-slate-500">
                        The assessment conversation will appear here.
                      </p>
                    )}

                    {conversation.map(
                      (item, index) => (

                        <div
                          key={item.id}
                          className={`rounded-2xl p-4 ${
                            item.role === "ai"
                              ? "bg-slate-800"
                              : "bg-indigo-500/10"
                          }`}
                        >

                          <p className="text-xs uppercase tracking-wider text-slate-500">
                            {item.role === "ai"
                              ? "AI Interviewer"
                              : "Candidate"}
                          </p>

                          <p className="mt-2 text-sm leading-relaxed text-slate-300">
                            {item.text}
                          </p>

                        </div>

                      )
                    )}

                  </div>

                </div>

                {/* END */}

                <div className="mt-8 flex justify-center">

                  <button
                    onClick={endAssessment}
                    disabled={ending}
                    className="rounded-xl bg-red-500 px-7 py-3 font-semibold hover:bg-red-600 disabled:opacity-50"
                  >
                    {ending
                      ? "Completing Assessment..."
                      : "End Assessment"}
                  </button>

                </div>

              </div>

            </section>

            {/* SIDEBAR */}

            <aside className="space-y-6">

              <div className="rounded-3xl border border-slate-800 bg-slate-900 p-6">

                <h3 className="text-lg font-semibold">
                  Behavioral Areas
                </h3>

                <div className="mt-5 space-y-3">

                  {BEHAVIORAL_AREAS.map(
                    (area) => (

                      <div
                        key={area}
                        className="rounded-xl border border-slate-800 bg-slate-950 p-3 text-sm text-slate-300"
                      >
                        {area}
                      </div>

                    )
                  )}

                </div>

              </div>

              <div className="rounded-3xl border border-slate-800 bg-slate-900 p-6">

                <h3 className="text-lg font-semibold">
                  Assessment
                </h3>

                <div className="mt-5 space-y-4 text-sm text-slate-400">

                  <p>
                    Speak naturally.
                  </p>

                  <p>
                    Give real examples.
                  </p>

                  <p>
                    Explain what you personally did.
                  </p>

                  <p>
                    Describe decisions and outcomes.
                  </p>

                  <p>
                    The AI will adapt the next scenario
                    based on your answer.
                  </p>

                </div>

              </div>

            </aside>

          </div>
        )}

        {ending && !assessmentFinished && (
          <div className="mt-8 rounded-2xl border border-indigo-500/20 bg-indigo-500/5 p-5 text-center text-slate-300">
            The AI is completing your assessment and preparing the report...
          </div>
        )}

      </main>

    </div>
  );
}

export default App;