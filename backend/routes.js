const express = require("express");
const path = require("path");
const fs = require("fs");
const createToken = require("./livekit");

const router = express.Router();

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
      if (
        typeof rawRoom !== "string" ||
        rawRoom.length > 128 ||
        !/^[a-zA-Z0-9_-]+$/.test(rawRoom)
      ) {
        return res.status(400).json({
          error:
            "Invalid room name. Must be alphanumeric with dashes or underscores, maximum 128 characters.",
        });
      }
    }

    const identity = rawIdentity || `candidate-${Date.now()}`;
    const { token, room } = await createToken(identity, rawRoom);

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

    if (
      typeof assessmentId !== "string" ||
      assessmentId.length > 128
    ) {
      return res.status(400).json({
        error: "Invalid assessment ID.",
      });
    }

    // Prevent path traversal by sanitizing file name
    const safeId = assessmentId.replace(/[^a-zA-Z0-9_-]/g, "_");

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

    res.json(parsed);
  } catch (error) {
    console.error("Failed to read assessment result:", error.message || error);
    res.status(500).json({
      error: "Failed to read assessment result.",
    });
  }
});

module.exports = router;