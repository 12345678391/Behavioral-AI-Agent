import { useEffect, useState, useRef } from "react";
import { getToken } from "./api";
import { joinRoom } from "./LiveKitRoom";
import {
  INTERVIEW_DURATION_SECONDS,
  WARNING_SECONDS,
  BEHAVIORAL_AREAS,
} from "./config";

function safeRandomUUID() {
  if (
    typeof window !== "undefined" &&
    window.crypto &&
    typeof window.crypto.randomUUID === "function"
  ) {
    try {
      return window.crypto.randomUUID();
    } catch {}
  }
  return (
    "id-" +
    Date.now().toString(36) +
    "-" +
    Math.random().toString(36).substring(2, 9)
  );
}

function App() {
  const [room, setRoom] = useState(null);
  const roomRef = useRef(null);
  const isConnectingRef = useRef(false);
  const [isConnecting, setIsConnecting] = useState(false);

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
  // CLEANUP ON UNMOUNT
  // ==========================================================

  useEffect(() => {
    return () => {
      if (roomRef.current) {
        console.log("[App] Room disconnection requested due to component unmount.");
        try {
          roomRef.current.disconnect();
        } catch (e) {
          console.warn("[App] Error disconnecting room on unmount:", e);
        }
        roomRef.current = null;
      }
    };
  }, []);

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
    // CANDIDATE INTERIM TRANSCRIPTION (REAL-TIME PROGRESSIVE)
    // --------------------------------------------------------

    if (
      type === "candidate_interim_transcript" ||
      type === "candidate_answer_interim" ||
      (type === "candidate_answer" && eventData.is_final === false)
    ) {
      const interimText = (eventData.answer || eventData.text || "").trim();

      if (!interimText) {
        return;
      }

      setConversation((previous) => {
        const interimIndex = previous.findIndex((item) => item.isInterim);

        if (interimIndex !== -1) {
          if (previous[interimIndex].text === interimText) {
            return previous;
          }
          const updated = [...previous];
          updated[interimIndex] = {
            ...updated[interimIndex],
            text: interimText,
          };
          return updated;
        }

        return [
          ...previous,
          {
            id: safeRandomUUID(),
            role: "candidate",
            text: interimText,
            isInterim: true,
          },
        ];
      });

      return;
    }

    // --------------------------------------------------------
    // CANDIDATE ANSWER TRANSCRIPT (FINAL)
    // --------------------------------------------------------

    if (type === "candidate_answer") {
      const answer = (eventData.answer || eventData.text || "").trim();

      if (!answer) {
        return;
      }

      setConversation((previous) => {
        const interimIndex = previous.findIndex((item) => item.isInterim);

        if (interimIndex !== -1) {
          const updated = [...previous];
          updated[interimIndex] = {
            ...updated[interimIndex],
            text: answer,
            isInterim: false,
          };
          return updated;
        }

        // Prevent duplicate final message if identical answer is already at the end
        const lastItem = previous[previous.length - 1];
        if (
          lastItem &&
          lastItem.role === "candidate" &&
          !lastItem.isInterim &&
          lastItem.text.trim().toLowerCase() === answer.toLowerCase()
        ) {
          return previous;
        }

        return [
          ...previous,
          {
            id: safeRandomUUID(),
            role: "candidate",
            text: answer,
            isInterim: false,
          },
        ];
      });

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
          (previous) => {
            const cleaned = previous.map((item) =>
              item.isInterim ? { ...item, isInterim: false } : item
            );

            return [
              ...cleaned,
              {
                id: safeRandomUUID(),
                role: "ai",
                text: question,
              },
            ];
          }
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
            id: safeRandomUUID(),
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
            id: safeRandomUUID(),
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
      const stage = eventData?.stage || "final_analysis";
      const code = eventData?.code || eventData?.error_type || "ASSESSMENT_ERROR";
      const httpStatus = eventData?.http_status || "";
      const message =
        eventData?.message ||
        "The interview was completed, but the final behavioral analysis could not be generated.";
      const details = eventData?.error_details || "";

      console.error(
        `[App] ASSESSMENT ERROR in stage '${stage}' [code: ${code}${httpStatus ? `, status: ${httpStatus}` : ""}]: ${message}` +
          (details ? ` (details: ${details})` : ""),
        { stage, code, httpStatus, message, details }
      );

      // Preserve recoverable conversation transcript if provided
      if (
        eventData?.recoverable_data?.transcript &&
        Array.isArray(eventData.recoverable_data.transcript) &&
        eventData.recoverable_data.transcript.length > 0
      ) {
        setConversation(
          eventData.recoverable_data.transcript.map((item) => ({
            id: safeRandomUUID(),
            role: item.role === "candidate" ? "user" : item.role,
            text: item.text,
          }))
        );
      }

      setErrorMessage(
        `${message}${details ? ` (Details: ${details})` : ""}`
      );

      setEnding(false);
      setConnected(false);
      // Mark as finished so results/completion review is displayed with recoverable data
      // and onDisconnected does not overwrite the message
      setAssessmentFinished(true);

      const activeRoom = roomRef.current || room;
      if (activeRoom) {
        console.log("[App] Room disconnection requested due to assessment_error.");
        try {
          activeRoom.disconnect();
        } catch (e) {
          console.warn("[App] Error disconnecting room on error:", e);
        }
        roomRef.current = null;
        setRoom(null);
      }

      return;
    }

    // --------------------------------------------------------
    // FINAL RESULT
    // --------------------------------------------------------

    if (
      type === "final_result"
    ) {
      const finalData = data.data || data;
      console.log(
        "[App] Final assessment result received:",
        finalData
      );

      setErrorMessage("");

      setAssessmentResult(
        finalData
      );

      setAssessmentFinished(true);
      setConnected(false);
      setEnding(false);

      const activeRoom = roomRef.current || room;
      if (activeRoom) {
        console.log("[App] Room disconnection requested due to final_result.");
        try {
          activeRoom.disconnect();
        } catch (e) {
          console.warn("[App] Error disconnecting room on final result:", e);
        }
        roomRef.current = null;
        setRoom(null);
      }

      return;
    }
  }

  // ==========================================================
  // START ASSESSMENT
  // ==========================================================

  async function startAssessment() {
    if (isConnectingRef.current || roomRef.current) {
      console.warn("[App] Assessment start request ignored: connection already in progress or room active.");
      return;
    }

    isConnectingRef.current = true;
    setIsConnecting(true);
    console.log("[App] Assessment start requested.");

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
              "[App] Assessment room connected."
            );

            setConnected(true);
          },
          (reason) => {
            console.log(
              "[App] Assessment room disconnected. Reason:",
              reason
            );

            roomRef.current = null;
            setConnected(false);
            setRoom(null);
            setEnding(false);

            // Safely finalize any lingering interim candidate message upon disconnect
            setConversation((previous) =>
              previous
                .map((item) => (item.isInterim ? { ...item, isInterim: false } : item))
                .filter((item) => item.text && item.text.trim())
            );

            setAssessmentFinished((finished) => {
              if (!finished) {
                setErrorMessage(
                  (prev) => prev || "Connection to the assessment was closed."
                );
              }
              return finished;
            });
          },
          (micError) => {
            console.warn(
              "[App] Microphone setup warning:",
              micError?.message || micError
            );

            setErrorMessage(
              "Microphone setup warning: " +
                (micError?.message ||
                  "Please verify your microphone is connected and browser permissions are allowed.")
            );
          }
        );

      roomRef.current = newRoom;
      setRoom(newRoom);

      setConnected(true);

      setAssessmentStarted(true);

    } catch (error) {
      console.error(
        "[App] Connection error during assessment startup:",
        error
      );

      setErrorMessage(
        error?.message
          ? `Could not start the assessment: ${error.message}`
          : "Could not start the assessment. Make sure the Node.js server and Python AI agent are running."
      );
    } finally {
      isConnectingRef.current = false;
      setIsConnecting(false);
    }
  }

  // ==========================================================
  // END ASSESSMENT
  // ==========================================================

  async function endAssessment() {
    const activeRoom = roomRef.current || room;
    console.log("[App] Assessment completion requested. Active room present:", !!activeRoom);
    if (!activeRoom || ending) {
      return;
    }

    try {
      setEnding(true);

      console.log(
        "[App] Publishing end_assessment request to agent..."
      );

      const payload =
        new TextEncoder().encode(
          JSON.stringify({
            type: "end_assessment",
          })
        );

      await activeRoom.localParticipant.publishData(
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
  // ASSESSMENT COMPLETED WITH NOTICE (PRESERVE RECOVERABLE DATA)
  // ==========================================================

  if (
    assessmentFinished &&
    !assessmentResult
  ) {
    return (
      <div className="min-h-screen bg-slate-950 text-white">
        <header className="border-b border-slate-800">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
            <div>
              <h1 className="text-2xl font-bold">
                Behavioral AI
              </h1>
              <p className="mt-1 text-sm text-slate-400">
                Assessment Status
              </p>
            </div>

            <div className="rounded-full bg-amber-500/10 px-4 py-2 text-sm text-amber-400">
              Completed with Notice
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-5xl px-6 py-10">
          {errorMessage && (
            <div className="mb-8 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-6 text-amber-200">
              <h3 className="text-lg font-semibold text-amber-300">
                Assessment Data Saved
              </h3>
              <p className="mt-2 text-sm leading-relaxed">
                {errorMessage}
              </p>
              <p className="mt-2 text-xs text-amber-300/80">
                Your interview responses were recorded and preserved on the server. You can review your completed conversation below.
              </p>
            </div>
          )}

          <section className="rounded-3xl border border-slate-800 bg-slate-900 p-8">
            <h2 className="text-xl font-bold">
              Interview Transcript
            </h2>
            <p className="mt-1 text-sm text-slate-400">
              Recorded responses from your assessment session:
            </p>

            <div className="mt-6 max-h-[500px] space-y-4 overflow-y-auto pr-2">
              {conversation.length === 0 ? (
                <p className="text-sm text-slate-500">
                  No conversation turns recorded.
                </p>
              ) : (
                conversation.map((item) => (
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
                ))
              )}
            </div>

            <div className="mt-8 flex justify-end">
              <button
                onClick={startNewAssessment}
                className="rounded-xl bg-indigo-500 px-6 py-3 font-semibold text-white hover:bg-indigo-600 transition"
              >
                Start New Assessment
              </button>
            </div>
          </section>
        </main>
      </div>
    );
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
              disabled={isConnecting}
              className={`mt-8 rounded-xl px-8 py-4 font-semibold transition ${
                isConnecting
                  ? "bg-indigo-500/50 cursor-not-allowed opacity-75"
                  : "bg-indigo-500 hover:bg-indigo-600"
              }`}
            >
              {isConnecting ? (
                <span className="flex items-center justify-center gap-2">
                  <svg
                    className="h-5 w-5 animate-spin"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-75"
                      fill="currentColor"
                      d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
                    />
                  </svg>
                  Connecting to AI Interviewer...
                </span>
              ) : (
                "Start Assessment"
              )}
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
                            {item.isInterim && (
                              <span className="ml-2 font-normal lowercase italic text-indigo-400">
                                speaking...
                              </span>
                            )}
                          </p>

                          <p
                            className={`mt-2 text-sm leading-relaxed ${
                              item.isInterim
                                ? "text-indigo-200"
                                : "text-slate-300"
                            }`}
                          >
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