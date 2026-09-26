const express = require("express");
const createToken = require("./livekit");

const router = express.Router();

router.get("/token", async (req, res) => {
  const identity = req.query.identity || "user";
  const token = await createToken(identity);

  res.json({
    token,
    serverUrl: process.env.LIVEKIT_URL,
  });
});

module.exports = router;