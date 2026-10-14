const express = require("express");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");
const createToken = require("./livekit");
const {
  startRecording,
  stopRecording,
  getRecordingStatus,
  getRecordingSession,
} = require("./recording");

const router = express.Router();

// ============================================================
// ASSESSMENT RESULT CACHE & VALIDATION HELPERS
// ============================================================
// IMPORTANT ARCHITECTURAL LIMITATION:
// Both assessmentResultsCache (in-memory Map) and local disk files under
// agent/assessments/ only bridge cross-container requests while the Render
// backend instance remains running. Neither in-memory state nor container-local
// ephemeral storage survives Render container restarts, redeployments, or
// free-tier spin-downs without an external durable database (e.g., PostgreSQL/Redis).
const CACHE_MAX_ENTRIES = 100;
const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour
const assessmentResultsCache = new Map();

function sanitizeEnvValue(val) {
  if (!val || typeof val !== "string") return "";
  let cleaned = val.trim().replace(/[\r\n]/g, "").trim();
  if (
    (cleaned.startsWith('"') && cleaned.endsWith('"')) ||
    (cleaned.startsWith("'") && cleaned.endsWith("'"))
  ) {
    cleaned = cleaned.slice(1, -1).trim();
  }
  return cleaned;
}

function getSyncSecret() {
  return (
    sanitizeEnvValue(process.env.ASSESSMENT_SYNC_SECRET) ||
    sanitizeEnvValue(process.env.LIVEKIT_API_SECRET)
  );
}

function computeSyncSignature(assessmentId, secret) {
  return crypto
    .createHmac("sha256", sanitizeEnvValue(secret))
    .update(String(assessmentId).trim())
    .digest("hex");
}

function verifySyncSignature(assessmentId, providedSignature) {
  const secret = getSyncSecret();
  if (!secret) {
    return true;
  }
  if (!providedSignature || typeof providedSignature !== "string") {
    return false;
  }
  const expected = computeSyncSignature(assessmentId, secret);
  const expectedBuf = Buffer.from(expected, "utf8");
  const providedBuf = Buffer.from(providedSignature.trim(), "utf8");
  if (expectedBuf.length !== providedBuf.length) {
    return false;
  }
  return crypto.timingSafeEqual(expectedBuf, providedBuf);
}

function isValidAssessmentId(assessmentId) {
  return (
    typeof assessmentId === "string" &&
    assessmentId.length > 0 &&
    assessmentId.length <= 128 &&
    /^[a-zA-Z0-9_-]+$/.test(assessmentId)
  );
}

function isValidCompletedResult(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return false;
  }
  if (data.completed !== true) {
    return false;
  }
  const analysis = data.final_analysis || data.finalAnalysis;
  if (!analysis || typeof analysis !== "object" || Array.isArray(analysis)) {
    return false;
  }
  if (
    typeof analysis.overall_score !== "number" ||
    !Number.isFinite(analysis.overall_score)
  ) {
    return false;
  }
  if (
    !analysis.scores ||
    typeof analysis.scores !== "object" ||
    Array.isArray(analysis.scores)
  ) {
    return false;
  }
  return true;
}

function pruneAssessmentCache(now = Date.now()) {
  for (const [key, entry] of assessmentResultsCache.entries()) {
    if (!entry || now - entry.timestamp > CACHE_TTL_MS) {
      assessmentResultsCache.delete(key);
    }
  }
  while (assessmentResultsCache.size >= CACHE_MAX_ENTRIES) {
    const oldestKey = assessmentResultsCache.keys().next().value;
    if (oldestKey === undefined) break;
    assessmentResultsCache.delete(oldestKey);
  }
}

function setCachedAssessmentResult(safeId, resultData, now = Date.now()) {
  if (assessmentResultsCache.has(safeId)) {
    assessmentResultsCache.delete(safeId);
  }
  pruneAssessmentCache(now);
  assessmentResultsCache.set(safeId, {
    data: resultData,
    timestamp: now,
  });
}

function getCachedAssessmentResult(safeId, now = Date.now()) {
  const entry = assessmentResultsCache.get(safeId);
  if (!entry) return null;
  if (now - entry.timestamp > CACHE_TTL_MS) {
    assessmentResultsCache.delete(safeId);
    return null;
  }
  return entry.data;
}

// ============================================================
// TOKEN GENERATION
// ============================================================
router.get("/token", async (req, res) => {
  try {
    const rawIdentity = req.query.identity;
    const rawRoom = req.query.room;

    // Validate identity if provided
    if (rawIdentity) {
      if (
        typeof rawIdentity !== "string" ||
        rawIdentity.length > 64 ||
        !/^[a-zA-Z0-9_-]+$/.test(rawIdentity)
      ) {
        return res.status(400).json({
          error:
            "Invalid identity. Must be alphanumeric with dashes or underscores, maximum 64 characters.",
        });
      }
    }

    // Validate room if provided
    if (rawRoom) {
      if (!isValidAssessmentId(rawRoom)) {
        return res.status(400).json({
          error:
            "Invalid room name. Must be alphanumeric with dashes or underscores, maximum 128 characters.",
        });
      }
    }

    const identity = rawIdentity || `candidate-${Date.now()}`;
    const { token, room } = await createToken(identity, rawRoom);

    // Wake up the Python agent service on Render in the background if asleep on free tier
    const agentUrl = process.env.AGENT_URL || "https://behavioral-agent.onrender.com";
    if (agentUrl && typeof fetch === "function") {
      fetch(`${agentUrl.replace(/\/+$/, "")}/health`).catch(() => {});
    }

    res.json({
      token,
      room,
      serverUrl: process.env.LIVEKIT_URL,
    });
  } catch (error) {
    console.error("Token generation error:", error.message || error);
    res.status(500).json({
      error: error.message || "Failed to generate LiveKit token.",
    });
  }
});

// ============================================================
// ASSESSMENT RESULT STORAGE (CROSS-CONTAINER SYNC)
// ============================================================
router.post("/assessment-result", express.json({ limit: "2mb" }), (req, res) => {
  try {
    const assessmentId =
      req.query.assessmentId ||
      req.body?.assessment_id ||
      req.body?.assessmentId;

    if (!assessmentId) {
      return res.status(400).json({
        error: "Assessment ID is required.",
      });
    }

    if (!isValidAssessmentId(assessmentId)) {
      return res.status(400).json({
        error:
          "Invalid assessment ID. Must be alphanumeric with dashes or underscores, maximum 128 characters.",
      });
    }

    const providedSignature = req.headers["x-assessment-signature"];
    if (!verifySyncSignature(assessmentId, providedSignature)) {
      return res.status(401).json({
        error: "Unauthorized assessment result submission.",
      });
    }

    const resultData = req.body;
    if (!isValidCompletedResult(resultData)) {
      return res.status(400).json({
        error:
          "Incomplete or invalid assessment result payload. A completed final_analysis is required.",
      });
    }

    const safeId = assessmentId.replace(/[^a-zA-Z0-9_-]/g, "_");

    // Link recording session if one was started for this assessment
    const recSession = getRecordingSession(safeId);
    if (recSession) {
      resultData.recording = {
        status: recSession.status,
        videoUrl: recSession.videoUrl,
        error: recSession.error,
      };
    } else if (!resultData.recording) {
      resultData.recording = {
        status: "unavailable",
        videoUrl: null,
        error: "No recording was started for this session.",
      };
    }

    // Cache in bounded memory map for immediate retrieval
    setCachedAssessmentResult(safeId, resultData);

    // Persist to local disk if directory is writable
    try {
      const assessmentsDir = path.join(
        __dirname,
        "..",
        "agent",
        "assessments"
      );
      if (!fs.existsSync(assessmentsDir)) {
        fs.mkdirSync(assessmentsDir, { recursive: true });
      }
      const resultPath = path.join(assessmentsDir, `${safeId}.json`);
      fs.writeFileSync(resultPath, JSON.stringify(resultData, null, 2), "utf-8");
    } catch (diskErr) {
      // Non-fatal if ephemeral filesystem is restricted; memory cache holds the result
      console.warn("Notice: assessment disk write omitted:", diskErr.message);
    }

    res.json({
      success: true,
      assessmentId: safeId,
    });
  } catch (error) {
    console.error("Failed to save assessment result:", error.message || error);
    res.status(500).json({
      error: "Failed to save assessment result.",
    });
  }
});

function syncRecordingToAssessment(roomName, recData) {
  if (!roomName || !recData) return;
  const safeId = roomName.replace(/[^a-zA-Z0-9_-]/g, "_");
  const cachedEntry = assessmentResultsCache.get(safeId);
  if (cachedEntry && cachedEntry.data) {
    cachedEntry.data.recording = {
      status: recData.status,
      videoUrl: recData.videoUrl,
      error: recData.error,
    };
  }
  try {
    const assessmentsDir = path.join(__dirname, "..", "agent", "assessments");
    const resultPath = path.join(assessmentsDir, `${safeId}.json`);
    if (fs.existsSync(resultPath)) {
      const content = fs.readFileSync(resultPath, "utf-8");
      const parsed = JSON.parse(content);
      parsed.recording = {
        status: recData.status,
        videoUrl: recData.videoUrl,
        error: recData.error,
      };
      fs.writeFileSync(resultPath, JSON.stringify(parsed, null, 2), "utf-8");
    }
  } catch (e) {
    // Non-fatal disk sync
  }
}

// ============================================================
// ASSESSMENT RESULT RETRIEVAL
// ============================================================
router.get("/assessment-result", (req, res) => {
  try {
    const assessmentId = req.query.assessmentId;

    if (!assessmentId) {
      return res.status(400).json({
        error: "Assessment ID is required.",
      });
    }

    if (!isValidAssessmentId(assessmentId)) {
      return res.status(400).json({
        error: "Invalid assessment ID.",
      });
    }

    const safeId = assessmentId.replace(/[^a-zA-Z0-9_-]/g, "_");
    const liveRec = getRecordingSession(safeId);

    // 1. Check bounded in-memory cache first (handles cross-container sync while process is live)
    const cached = getCachedAssessmentResult(safeId);
    if (cached && isValidCompletedResult(cached)) {
      if (liveRec) {
        cached.recording = {
          status: liveRec.status,
          videoUrl: liveRec.videoUrl,
          error: liveRec.error,
        };
      }
      return res.json(cached);
    }

    // 2. Check local disk
    const assessmentsDir = path.join(
      __dirname,
      "..",
      "agent",
      "assessments"
    );
    const resultPath = path.join(assessmentsDir, `${safeId}.json`);

    if (!fs.existsSync(resultPath)) {
      return res.status(404).json({
        error: "Assessment result not found or not yet generated.",
      });
    }

    const content = fs.readFileSync(resultPath, "utf-8");
    const parsed = JSON.parse(content);

    // Do not serve incomplete assessment snapshots (e.g. final_analysis: null) as completed reports
    if (!isValidCompletedResult(parsed)) {
      return res.status(404).json({
        error: "Assessment result is incomplete or not yet finalized.",
      });
    }

    if (liveRec) {
      parsed.recording = {
        status: liveRec.status,
        videoUrl: liveRec.videoUrl,
        error: liveRec.error,
      };
    }

    res.json(parsed);
  } catch (error) {
    console.error("Failed to read assessment result:", error.message || error);
    res.status(500).json({
      error: "Failed to read assessment result.",
    });
  }
});

// ============================================================
// INTERVIEW RECORDING ENDPOINTS
// ============================================================
router.post("/recording/start", express.json(), async (req, res) => {
  try {
    const room = req.body?.room;
    if (!room || !isValidAssessmentId(room)) {
      return res.status(400).json({
        error: "Valid room name is required to start recording.",
      });
    }

    const result = await startRecording(room);
    syncRecordingToAssessment(room, result);
    res.json(result);
  } catch (error) {
    console.error("Recording start endpoint error:", error.message || error);
    res.status(500).json({
      success: false,
      status: "failed",
      error: "Failed to start recording.",
    });
  }
});

router.post("/recording/stop", express.json(), async (req, res) => {
  try {
    const room = req.body?.room;
    if (!room || !isValidAssessmentId(room)) {
      return res.status(400).json({
        error: "Valid room name is required to stop recording.",
      });
    }

    const session = await stopRecording(room);
    syncRecordingToAssessment(room, session);
    res.json({
      success: true,
      room,
      status: session.status,
      videoUrl: session.videoUrl,
      error: session.error,
    });
  } catch (error) {
    console.error("Recording stop endpoint error:", error.message || error);
    res.status(500).json({
      success: false,
      status: "failed",
      error: "Failed to stop recording.",
    });
  }
});

router.get("/recording/status", async (req, res) => {
  try {
    const room = req.query.room;
    if (!room || !isValidAssessmentId(room)) {
      return res.status(400).json({
        error: "Valid room name is required to get recording status.",
      });
    }

    const statusResult = await getRecordingStatus(room);
    syncRecordingToAssessment(room, statusResult);
    res.json(statusResult);
  } catch (error) {
    console.error("Recording status endpoint error:", error.message || error);
    res.status(500).json({
      success: false,
      status: "failed",
      error: "Failed to retrieve recording status.",
    });
  }
});

router.assessmentResultsCache = assessmentResultsCache;
router.CACHE_MAX_ENTRIES = CACHE_MAX_ENTRIES;
router.CACHE_TTL_MS = CACHE_TTL_MS;
router.computeSyncSignature = computeSyncSignature;
router.isValidCompletedResult = isValidCompletedResult;
router.pruneAssessmentCache = pruneAssessmentCache;
router.setCachedAssessmentResult = setCachedAssessmentResult;
router.getCachedAssessmentResult = getCachedAssessmentResult;

module.exports = router;