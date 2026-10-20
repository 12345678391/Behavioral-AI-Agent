import { useEffect, useState, useRef, useCallback } from "react";
import {
  getToken,
  getAssessmentResult,
  startRecording,
  stopRecording,
  getRecordingStatus,
} from "./api";
import { joinRoom, pauseRoomAudio, resumeRoomAudio } from "./LiveKitRoom";
import CameraPreview from "./CameraPreview";
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

export function isValidAssessmentReport(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return false;
  }
  if (data.completed === false) {
    return false;
  }
  const analysis = data.final_analysis || data.finalAnalysis;
  if (!analysis || typeof analysis !== "object" || Array.isArray(analysis)) {
    return false;
  }
  return (
    typeof analysis.overall_score === "number" &&
    Number.isFinite(analysis.overall_score) &&
    Boolean(
      analysis.scores &&
        typeof analysis.scores === "object" &&
        !Array.isArray(analysis.scores)
    )
  );
}

function App() {
  const [room, setRoom] = useState(null);
  const roomRef = useRef(null);
  const isConnectingRef = useRef(false);
  const endingRef = useRef(false);
  const assessmentFinishedRef = useRef(false);
  const assessmentResultRef = useRef(null);
  const currentRoomNameRef = useRef("");
  const retryTimerRef = useRef(null);
  const isMountedRef = useRef(true);
  const [isConnecting, setIsConnecting] = useState(false);
  const [retryingReport, setRetryingReport] = useState(false);

  const [isPaused, setIsPaused] = useState(false);
  const isPausedRef = useRef(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const isTransitioningRef = useRef(false);

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

  const [audioBlocked, setAudioBlocked] =
    useState(false);

  const [recordingStatus, setRecordingStatus] = useState("idle"); // 'idle' | 'starting' | 'active' | 'finalizing' | 'completed' | 'failed' | 'unavailable'
  const [recordingVideoUrl, setRecordingVideoUrl] = useState(null);
  const [recordingError, setRecordingError] = useState("");
  const recordingPollingRef = useRef(null);
  const recordingStartedRoomRef = useRef(null);
  const recordingStoppedRoomRef = useRef(null);
  const archiveTokenRef = useRef("");

  const clearRecordingPolling = useCallback(() => {
    if (recordingPollingRef.current) {
      clearInterval(recordingPollingRef.current);
      recordingPollingRef.current = null;
    }
  }, []);

  const checkRecordingStatus = useCallback(async (roomName) => {
    if (!roomName) return;
    try {
      const token = archiveTokenRef.current || undefined;
      const res = await getRecordingStatus(roomName, token);
      if (!isMountedRef.current) return;
      if (res && res.status) {
        setRecordingStatus(res.status);
        if (res.videoUrl) {
          setRecordingVideoUrl(res.videoUrl);
        }
        if (res.error) {
          setRecordingError(res.error);
        }
        if (
          res.status === "completed" ||
          res.status === "failed" ||
          res.status === "unavailable"
        ) {
          clearRecordingPolling();
        }
      }
    } catch (err) {
      // Non-fatal status polling notice
    }
  }, [clearRecordingPolling]);

  const pollRecordingUntilFinalized = useCallback(
    (roomName, maxAttempts = 40, intervalMs = 2500) => {
      if (!roomName) return;
      clearRecordingPolling();

      let attempts = 0;
      checkRecordingStatus(roomName);

      recordingPollingRef.current = setInterval(async () => {
        attempts++;
        if (!isMountedRef.current || attempts >= maxAttempts) {
          clearRecordingPolling();
          return;
        }
        await checkRecordingStatus(roomName);
      }, intervalMs);
    },
    [clearRecordingPolling, checkRecordingStatus]
  );

  const safeStartRecording = useCallback(async (roomName) => {
    if (!roomName || typeof startRecording !== "function") return;
    if (recordingStartedRoomRef.current === roomName) return;
    recordingStartedRoomRef.current = roomName;
    try {
      setRecordingStatus("starting");
      const res = await startRecording(roomName);
      if (!isMountedRef.current) return;
      if (res && res.status) {
        setRecordingStatus(res.status);
        if (res.error) setRecordingError(res.error);
      }
    } catch (err) {
      if (!isMountedRef.current) return;
      console.warn("[App] Recording start notice:", err?.message || err);
      setRecordingStatus("failed");
      setRecordingError(err?.message || "Could not start recording.");
    }
  }, []);

  const safeStopRecording = useCallback(async (roomName) => {
    if (!roomName || typeof stopRecording !== "function") return;
    if (recordingStoppedRoomRef.current === roomName) return;
    recordingStoppedRoomRef.current = roomName;
    try {
      const res = await stopRecording(roomName);
      if (!isMountedRef.current) return;
      if (res && res.status) {
        setRecordingStatus(res.status);
        if (res.videoUrl) setRecordingVideoUrl(res.videoUrl);
        if (res.error) setRecordingError(res.error);
      }
    } catch (err) {
      // Non-fatal stop recording notice
    }
  }, []);

  // Synchronously prime the Web Audio pipeline during a user gesture
  function unlockBrowserAudio() {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) {
        const ctx = new AudioCtx();
        if (ctx.state === "suspended") {
          ctx.resume();
        }
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        gain.gain.value = 0;
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(0);
        osc.stop(0.001);
      }
    } catch (e) {
      console.warn("[App] Browser audio unlock notice:", e);
    }
  }

  // Explicit user gesture to unmute / resume room audio if blocked by autoplay policy
  async function enableAudio() {
    const activeRoom = roomRef.current || room;
    if (activeRoom) {
      console.log("[App] Explicit user gesture to enable AI audio...");
      try {
        await activeRoom.startAudio();
        console.log("[App] activeRoom.startAudio() succeeded.");
        setAudioBlocked(false);
      } catch (err) {
        console.warn("[App] Error enabling audio:", err);
      }
    }
  }

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
    isMountedRef.current = true;

    // Check for capability token in URL hash for authorized archive reopening
    if (typeof window !== "undefined" && window.location?.hash) {
      try {
        const hashParams = new URLSearchParams(
          window.location.hash.replace(/^#/, "")
        );
        const hashToken =
          hashParams.get("archiveToken") ||
          hashParams.get("token") ||
          hashParams.get("key");
        const hashRoom = hashParams.get("room");
        if (hashToken) {
          archiveTokenRef.current = hashToken;
        }
        if (hashRoom && !currentRoomNameRef.current) {
          currentRoomNameRef.current = hashRoom;
        }
        // Sanitize hash from address bar and history to prevent credential leakage
        if (hashToken || hashRoom) {
          window.history.replaceState(
            null,
            "",
            window.location.pathname + window.location.search
          );
        }
        if (hashRoom && hashToken) {
          setAssessmentFinished(true);
          fetchReportWithBoundedRetry(hashRoom, 3, 1000);
          checkRecordingStatus(hashRoom);
        } else if (hashRoom && !hashToken) {
          setAssessmentFinished(true);
          setErrorMessage(
            "An authorization token is required in the link hash to view this archived session."
          );
        }
      } catch (_) {
        // Non-fatal hash parse
      }
    }

    return () => {
      isMountedRef.current = false;
      clearRecordingPolling();
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
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
  }, [clearRecordingPolling, checkRecordingStatus]);

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
    // INTERVIEW PAUSED / RESUMED (AGENT SYNC)
    // --------------------------------------------------------

    if (type === "interview_paused") {
      isPausedRef.current = true;
      setIsPaused(true);
      isTransitioningRef.current = false;
      setIsTransitioning(false);

      if (typeof eventData.remaining_seconds === "number") {
        setTimeLeft(eventData.remaining_seconds);
      }

      return;
    }

    if (type === "interview_resumed") {
      isPausedRef.current = false;
      setIsPaused(false);
      isTransitioningRef.current = false;
      setIsTransitioning(false);

      if (typeof eventData.remaining_seconds === "number") {
        setTimeLeft(eventData.remaining_seconds);
      }

      return;
    }

    // --------------------------------------------------------
    // CANDIDATE INTERIM TRANSCRIPTION PROGRESSION
    // --------------------------------------------------------

    if (
      type === "candidate_interim_transcript" ||
      (type === "candidate_answer" && eventData.is_final === false)
    ) {
      const interimText = (eventData.answer || eventData.text || "").trim();
      if (!interimText) {
        return;
      }

      setConversation((previous) => {
        const existingIndex = previous.findIndex((item) => item.isInterim);
        if (existingIndex !== -1) {
          const updated = [...previous];
          updated[existingIndex] = {
            ...updated[existingIndex],
            text: interimText,
            isInterim: true,
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

      if (answer) {
        setConversation((previous) => {
          const existingIndex = previous.findIndex((item) => item.isInterim);
          if (existingIndex !== -1) {
            const updated = [...previous];
            updated[existingIndex] = {
              ...updated[existingIndex],
              text: answer,
              isInterim: false,
            };
            return updated.filter((item, idx) => idx === existingIndex || !item.isInterim);
          }

          // Merge or update if previous message is already a candidate answer in the active turn
          const lastItem = previous[previous.length - 1];
          if (
            lastItem &&
            lastItem.role === "candidate" &&
            !lastItem.isInterim
          ) {
            if (lastItem.text.toLowerCase() === answer.toLowerCase()) {
              return previous;
            }
            if (answer.toLowerCase().startsWith(lastItem.text.toLowerCase())) {
              const updated = [...previous];
              updated[updated.length - 1] = {
                ...lastItem,
                text: answer,
              };
              return updated;
            }
            if (lastItem.text.toLowerCase().includes(answer.toLowerCase())) {
              return previous;
            }
            const updated = [...previous];
            updated[updated.length - 1] = {
              ...lastItem,
              text: `${lastItem.text} ${answer}`.trim(),
            };
            return updated;
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
            ...previous.map((item) => (item.isInterim ? { ...item, isInterim: false } : item)),
            {
              id: safeRandomUUID(),
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
          ...previous.map((item) => (item.isInterim ? { ...item, isInterim: false } : item)),
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
          ...previous.map((item) => (item.isInterim ? { ...item, isInterim: false } : item)),
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
      assessmentFinishedRef.current = true;
      endingRef.current = false;
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

      if (isValidAssessmentReport(finalData)) {
        setErrorMessage("");
        assessmentResultRef.current = finalData;
        setAssessmentResult(finalData);

        if (finalData.recording) {
          if (finalData.recording.status) {
            setRecordingStatus(finalData.recording.status);
          }
          if (finalData.recording.videoUrl) {
            setRecordingVideoUrl(finalData.recording.videoUrl);
          }
          if (finalData.recording.error) {
            setRecordingError(finalData.recording.error);
          }
        }
      } else {
        console.warn("[App] Received incomplete final_result payload:", finalData);
        assessmentResultRef.current = null;
        setAssessmentResult(null);
        setErrorMessage(
          "The assessment completed, but the final behavioral report was incomplete."
        );
      }

      assessmentFinishedRef.current = true;
      endingRef.current = false;

      setAssessmentFinished(true);
      setConnected(false);
      setEnding(false);

      if (currentRoomNameRef.current) {
        safeStopRecording(currentRoomNameRef.current);
        pollRecordingUntilFinalized(currentRoomNameRef.current);
      }

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
  // BOUNDED REPORT FALLBACK POLLING
  // ==========================================================

  async function fetchReportWithBoundedRetry(
    roomName,
    maxAttempts = 5,
    retryDelayMs = 2000
  ) {
    if (!roomName) return false;
    if (isMountedRef.current) {
      setRetryingReport(true);
    }

    try {
      for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        if (!isMountedRef.current) return false;
        if (isValidAssessmentReport(assessmentResultRef.current)) {
          return true;
        }

        try {
          const token = archiveTokenRef.current || undefined;
          const res = await getAssessmentResult(roomName, token);
          const fallbackData = res?.data || res;
          if (isValidAssessmentReport(fallbackData)) {
            console.log(
              "[App] Retrieved assessment result via backend fallback:",
              fallbackData
            );
            assessmentResultRef.current = fallbackData;
            if (isMountedRef.current) {
              setAssessmentResult(fallbackData);
              setErrorMessage("");
              if (fallbackData.recording) {
                if (fallbackData.recording.status) {
                  setRecordingStatus(fallbackData.recording.status);
                }
                if (fallbackData.recording.videoUrl) {
                  setRecordingVideoUrl(fallbackData.recording.videoUrl);
                }
                if (fallbackData.recording.error) {
                  setRecordingError(fallbackData.recording.error);
                }
              }
            }
            return true;
          }
        } catch (err) {
          console.log(
            `[App] Backend result fallback attempt ${attempt}/${maxAttempts} notice:`,
            err?.message || err
          );
        }

        if (attempt < maxAttempts && isMountedRef.current) {
          await new Promise((resolve) => {
            retryTimerRef.current = setTimeout(resolve, retryDelayMs);
          });
        }
      }

      if (
        isMountedRef.current &&
        !isValidAssessmentReport(assessmentResultRef.current)
      ) {
        setErrorMessage(
          (prev) =>
            prev ||
            "The final assessment report could not be retrieved from the server. You can retry fetching the report or start a new assessment."
        );
      }
      return false;
    } finally {
      if (isMountedRef.current) {
        setRetryingReport(false);
      }
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

    // Warm up Web Audio during the user click gesture before async fetch
    unlockBrowserAudio();

    try {
      setErrorMessage("");
      setAudioBlocked(false);

      endingRef.current = false;
      assessmentFinishedRef.current = false;
      assessmentResultRef.current = null;
      currentRoomNameRef.current = "";

      setIsPaused(false);
      isPausedRef.current = false;
      setIsTransitioning(false);
      isTransitioningRef.current = false;

      clearRecordingPolling();
      recordingStartedRoomRef.current = null;
      recordingStoppedRoomRef.current = null;
      setRecordingStatus("idle");
      setRecordingVideoUrl(null);
      setRecordingError("");

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

      currentRoomNameRef.current = data?.room || "";
      if (data?.archiveToken) {
        archiveTokenRef.current = data.archiveToken;
      }

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

            if (currentRoomNameRef.current) {
              safeStartRecording(currentRoomNameRef.current);
            }
          },
          (reason) => {
            console.log(
              "[App] Assessment room disconnected. Reason:",
              reason
            );

            if (currentRoomNameRef.current) {
              safeStopRecording(currentRoomNameRef.current);
              pollRecordingUntilFinalized(currentRoomNameRef.current);
            }

            roomRef.current = null;
            setConnected(false);
            setRoom(null);
            setAudioBlocked(false);

            // Safely finalize any in-flight interim message upon disconnection
            setConversation((previous) =>
              previous.map((item) => (item.isInterim ? { ...item, isInterim: false } : item))
            );

            // If the assessment was completed or intentional ending is in progress, do not treat disconnection as failure
            if (
              isValidAssessmentReport(assessmentResultRef.current) ||
              assessmentFinishedRef.current
            ) {
              setEnding(false);
              setAssessmentFinished(true);
              return;
            }

            if (endingRef.current) {
              console.log("[App] Room disconnected during intentional assessment conclusion.");
              setEnding(false);
              setAssessmentFinished(true);
              // If final result wasn't received via data channel, attempt bounded fallback retrieval
              if (currentRoomNameRef.current) {
                fetchReportWithBoundedRetry(currentRoomNameRef.current, 5, 2000);
              }
              return;
            }

            setEnding(false);
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
          },
          (canPlay) => {
            console.log("[App] Audio playback status update. canPlay:", canPlay);
            setAudioBlocked(!canPlay);
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
  // PAUSE / RESUME ASSESSMENT
  // ==========================================================

  const togglePauseResume = useCallback(async () => {
    if (
      isTransitioningRef.current ||
      endingRef.current ||
      assessmentFinishedRef.current
    ) {
      console.warn("[App] Pause/Resume action ignored: state transition in progress or assessment ended.");
      return;
    }

    const activeRoom = roomRef.current || room;
    if (!activeRoom) {
      console.warn("[App] Pause/Resume action ignored: no active room present.");
      return;
    }

    const targetPaused = !isPausedRef.current;
    isTransitioningRef.current = true;
    setIsTransitioning(true);

    try {
      if (targetPaused) {
        // --- PAUSE INTERVIEW ---
        isPausedRef.current = true;
        setIsPaused(true);

        await pauseRoomAudio(activeRoom);

        try {
          const payload = new TextEncoder().encode(
            JSON.stringify({
              type: "pause_interview",
              room: currentRoomNameRef.current,
            })
          );
          await activeRoom.localParticipant.publishData(payload, {
            reliable: true,
            topic: "behavioral-ui",
          });
          console.log("[App] Published pause_interview request to agent.");
        } catch (pubErr) {
          console.warn("[App] Error publishing pause_interview:", pubErr);
        }
      } else {
        // --- RESUME INTERVIEW ---
        isPausedRef.current = false;
        setIsPaused(false);

        await resumeRoomAudio(activeRoom);

        try {
          const payload = new TextEncoder().encode(
            JSON.stringify({
              type: "resume_interview",
              room: currentRoomNameRef.current,
            })
          );
          await activeRoom.localParticipant.publishData(payload, {
            reliable: true,
            topic: "behavioral-ui",
          });
          console.log("[App] Published resume_interview request to agent.");
        } catch (pubErr) {
          console.warn("[App] Error publishing resume_interview:", pubErr);
        }
      }
    } catch (err) {
      console.error("[App] Failed to toggle pause/resume:", err);
    } finally {
      isTransitioningRef.current = false;
      setIsTransitioning(false);
    }
  }, [room]);

  // ==========================================================
  // END ASSESSMENT
  // ==========================================================

  async function endAssessment() {
    const activeRoom = roomRef.current || room;
    console.log("[App] Assessment completion requested. Active room present:", !!activeRoom);
    if (!activeRoom || ending || endingRef.current || assessmentFinished || assessmentFinishedRef.current) {
      return;
    }

    try {
      endingRef.current = true;
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

      endingRef.current = false;
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
    if (!connected || assessmentFinished || isPaused) {
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
    isPaused,
  ]);

  // ==========================================================
  // TIMER ZERO
  // ==========================================================

  useEffect(() => {
    if (
      !connected ||
      assessmentFinished ||
      isPaused
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
    isPaused,
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
    !isValidAssessmentReport(assessmentResult)
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

            <div className="mt-8 flex justify-end gap-4">
              {currentRoomNameRef.current && (
                <button
                  onClick={() =>
                    fetchReportWithBoundedRetry(
                      currentRoomNameRef.current,
                      3,
                      1500
                    )
                  }
                  disabled={retryingReport}
                  className="rounded-xl border border-slate-700 bg-slate-800 px-6 py-3 font-semibold text-slate-200 hover:bg-slate-700 disabled:opacity-50 transition"
                >
                  {retryingReport ? "Retrieving Report..." : "Retry Report Retrieval"}
                </button>
              )}
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
    isValidAssessmentReport(assessmentResult)
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

          {/* INTERVIEW RECORDING & PLAYBACK */}
          <section
            className="mt-8 rounded-3xl border border-slate-800 bg-slate-900 p-8"
            data-testid="recording-playback-section"
          >
            <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-sm uppercase tracking-widest text-indigo-400 font-semibold">
                  Session Archive
                </p>
                <h3 className="mt-1 text-2xl font-bold flex items-center gap-2">
                  <span aria-hidden="true">🎬</span> Interview Video Recording
                </h3>
              </div>

              <div className="flex items-center gap-2">
                <span
                  data-testid="recording-status-badge"
                  className={`rounded-full px-3 py-1 text-xs font-semibold ${
                    recordingStatus === "completed"
                      ? "border border-green-500/20 bg-green-500/10 text-green-400"
                      : recordingStatus === "finalizing" ||
                        recordingStatus === "active" ||
                        recordingStatus === "starting"
                      ? "border border-indigo-500/20 bg-indigo-500/10 text-indigo-300 animate-pulse"
                      : "border border-slate-700 bg-slate-800 text-slate-400"
                  }`}
                >
                  {recordingStatus === "completed"
                    ? "Recording Available"
                    : recordingStatus === "finalizing"
                    ? "Finalizing Recording..."
                    : recordingStatus === "active" || recordingStatus === "starting"
                    ? "Processing..."
                    : "Recording Unavailable"}
                </span>
              </div>
            </div>

            <p className="mt-3 text-sm text-slate-400">
              Review the synchronized camera and audio recording of your behavioral assessment.
            </p>

            <div className="mt-6">
              {recordingStatus === "completed" && recordingVideoUrl ? (
                <div className="space-y-4" data-testid="recording-player-container">
                  <div className="overflow-hidden rounded-2xl border border-slate-800 bg-black aspect-video flex items-center justify-center">
                    <video
                      controls
                      playsInline
                      src={recordingVideoUrl}
                      data-testid="recording-video-player"
                      className="w-full h-full object-contain"
                    >
                      Your browser does not support the video tag.
                    </video>
                  </div>

                  <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                    <p className="text-xs text-slate-400">
                      Recorded via LiveKit Room Composite Egress.
                    </p>

                    <a
                      href={recordingVideoUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      data-testid="recording-download-link"
                      className="inline-flex items-center gap-2 rounded-xl border border-indigo-500/30 bg-indigo-500/10 px-4 py-2 text-xs font-semibold text-indigo-300 transition hover:bg-indigo-500/20"
                    >
                      <span aria-hidden="true">⬇️</span> Download Recording (.mp4)
                    </a>
                  </div>
                </div>
              ) : recordingStatus === "finalizing" ||
                recordingStatus === "active" ||
                recordingStatus === "starting" ? (
                <div
                  className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-800 bg-slate-950/60 p-10 text-center"
                  data-testid="recording-finalizing-container"
                >
                  <div className="h-10 w-10 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
                  <h4 className="mt-4 text-base font-semibold text-slate-200">
                    Finalizing Interview Recording...
                  </h4>
                  <p className="mt-2 max-w-md text-xs text-slate-400">
                    The video stream is currently being encoded and uploaded to storage. This usually takes a few moments.
                  </p>

                  <button
                    type="button"
                    onClick={() => {
                      const roomName =
                        currentRoomNameRef.current ||
                        assessmentResult?.assessment_id;
                      if (roomName) checkRecordingStatus(roomName);
                    }}
                    data-testid="check-recording-status-btn"
                    className="mt-5 rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-xs font-medium text-slate-200 transition hover:bg-slate-700 hover:text-white"
                  >
                    Check Status Now
                  </button>
                </div>
              ) : (
                <div
                  className="rounded-2xl border border-slate-800 bg-slate-950/40 p-6 text-center"
                  data-testid="recording-unavailable-container"
                >
                  <span className="text-3xl mb-2 inline-block" aria-hidden="true">
                    📹
                  </span>
                  <h4 className="text-base font-semibold text-slate-300">
                    Recording Unavailable
                  </h4>
                  <p className="mt-2 max-w-lg mx-auto text-xs text-slate-400 leading-relaxed">
                    {recordingError ||
                      assessmentResult?.recording?.error ||
                      "No interview video recording is available for this session. Cloud storage or LiveKit Egress may not be configured."}
                  </p>

                  {(currentRoomNameRef.current || assessmentResult?.assessment_id) && (
                    <button
                      type="button"
                      onClick={() => {
                        const roomName =
                          currentRoomNameRef.current ||
                          assessmentResult?.assessment_id;
                        if (roomName) checkRecordingStatus(roomName);
                      }}
                      data-testid="retry-recording-status-btn"
                      className="mt-4 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-semibold text-slate-300 transition hover:bg-slate-700 hover:text-white"
                    >
                      Refresh Recording Status
                    </button>
                  )}
                </div>
              )}
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

          <div className="flex items-center gap-3">
            {connected && (
              <span
                className={`rounded-full px-3 py-1.5 text-xs font-medium ${
                  audioBlocked
                    ? "border border-amber-500/30 bg-amber-500/10 text-amber-300"
                    : "bg-indigo-500/10 text-indigo-300"
                }`}
              >
                {audioBlocked ? "🔇 Audio Blocked" : "🔊 Audio Active"}
              </span>
            )}
            <div
              data-testid="interview-status-badge"
              className={`rounded-full px-4 py-2 text-sm font-semibold ${
                assessmentFinished
                  ? "bg-indigo-500/10 text-indigo-400 border border-indigo-500/20"
                  : isPaused
                  ? "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                  : connected
                  ? "bg-green-500/10 text-green-400 border border-green-500/20"
                  : "bg-slate-800 text-slate-400"
              }`}
            >
              {assessmentFinished
                ? "Completed"
                : isPaused
                ? "Paused"
                : connected
                ? "Assessment Active (Running)"
                : "Ready"}
            </div>
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

                {audioBlocked && (
                  <div className="mt-6 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-amber-500/40 bg-amber-500/10 p-5 text-amber-200">
                    <div className="flex items-center gap-3">
                      <span className="text-2xl">🔇</span>
                      <div>
                        <p className="text-sm font-semibold text-amber-300">
                          AI Interviewer audio is paused by your browser
                        </p>
                        <p className="mt-0.5 text-xs text-amber-300/80">
                          Browser autoplay policy requires an explicit click to allow voice playback.
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={enableAudio}
                      className="rounded-xl bg-amber-500 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-amber-400"
                    >
                      🔊 Enable AI Audio
                    </button>
                  </div>
                )}

                {isPaused && (
                  <div
                    data-testid="paused-banner"
                    className="mt-6 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-5 text-amber-200"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-2xl" aria-hidden="true">⏸️</span>
                      <div>
                        <p className="text-sm font-semibold text-amber-300">
                          Interview is currently paused
                        </p>
                        <p className="mt-0.5 text-xs text-amber-300/80">
                          Countdown timer and AI question progression are frozen. Your microphone is muted.
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={togglePauseResume}
                      disabled={isTransitioning}
                      data-testid="banner-resume-button"
                      className="rounded-xl bg-amber-500 px-5 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-amber-400 disabled:opacity-50"
                    >
                      {isTransitioning ? "Resuming..." : "▶️ Resume Interview"}
                    </button>
                  </div>
                )}

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
                          className={`rounded-2xl p-4 transition-all duration-150 ${
                            item.role === "ai"
                              ? "bg-slate-800"
                              : item.isInterim
                              ? "bg-indigo-500/20 border border-indigo-400/30"
                              : "bg-indigo-500/10"
                          }`}
                        >

                          <div className="flex items-center justify-between">
                            <p className="text-xs uppercase tracking-wider text-slate-500">
                              {item.role === "ai"
                                ? "AI Interviewer"
                                : "Candidate"}
                            </p>
                            {item.isInterim && (
                              <span className="flex items-center gap-1.5 text-xs text-indigo-400 font-medium">
                                <span className="inline-block h-1.5 w-1.5 rounded-full bg-indigo-400 animate-pulse"></span>
                                speaking...
                              </span>
                            )}
                          </div>

                          <p className={`mt-2 text-sm leading-relaxed ${item.isInterim ? "text-slate-200 italic" : "text-slate-300"}`}>
                            {item.text}
                          </p>

                        </div>

                      )
                    )}

                  </div>

                </div>

                {/* ACTIONS: PAUSE / RESUME & END */}

                <div className="mt-8 flex flex-wrap items-center justify-center gap-4">

                  <button
                    type="button"
                    onClick={togglePauseResume}
                    disabled={isTransitioning || ending || assessmentFinished}
                    data-testid="pause-resume-button"
                    className={`rounded-xl px-7 py-3 font-semibold transition ${
                      isPaused
                        ? "bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold shadow-lg shadow-amber-500/20"
                        : "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
                    } disabled:opacity-50 disabled:cursor-not-allowed`}
                  >
                    {isTransitioning
                      ? isPaused
                        ? "Resuming..."
                        : "Pausing..."
                      : isPaused
                      ? "▶️ Resume Interview"
                      : "⏸️ Pause / Mute"}
                  </button>

                  <button
                    type="button"
                    onClick={endAssessment}
                    disabled={ending || assessmentFinished}
                    data-testid="end-assessment-button"
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

              <CameraPreview isActive={connected && !assessmentFinished && !ending} />

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