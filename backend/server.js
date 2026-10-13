const path = require("path");
require("dotenv").config({ path: path.join(__dirname, ".env") });
require("dotenv").config({ path: path.join(__dirname, "..", ".env") });

const express = require("express");
const cors = require("cors");
const routes = require("./routes");

const app = express();
const PORT = process.env.PORT || 5000;

// ============================================================
// CORS CONFIGURATION
// ============================================================
const PRODUCTION_ORIGIN = "https://behavioral-ai-agent-src.vercel.app";

const configuredOrigins = (process.env.FRONTEND_ORIGIN || "")
  .split(",")
  .map((origin) => origin.trim().replace(/\/+$/, ""))
  .filter(Boolean);

const allowedOrigins = Array.from(
  new Set([PRODUCTION_ORIGIN, ...configuredOrigins])
);

function isOriginAllowed(origin) {
  // Allow non-browser requests (tools, curl, server-to-server, health checks)
  if (!origin) return true;

  const normalized = origin.trim().replace(/\/+$/, "");

  // 1. Exact match against production or configured origins
  if (allowedOrigins.includes(normalized)) {
    return true;
  }

  // 2. Local development origins (localhost or 127.0.0.1 on any port)
  const isLocalhost =
    /^http:\/\/localhost(:\d+)?$/i.test(normalized) ||
    /^http:\/\/127\.0\.0\.1(:\d+)?$/i.test(normalized);
  if (isLocalhost) {
    return true;
  }

  // 3. Safe, explicit Vercel preview deployments for this project
  const isVercelPreview =
    /^https:\/\/behavioral-ai-agent-src(-[a-zA-Z0-9_-]+)?\.vercel\.app$/i.test(
      normalized
    );
  if (isVercelPreview) {
    return true;
  }

  return false;
}

const corsOptions = {
  origin: (origin, callback) => {
    if (isOriginAllowed(origin)) {
      return callback(null, true);
    }
    callback(new Error("CORS request blocked by policy."));
  },
  credentials: true,
  methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
  allowedHeaders: ["Content-Type", "Authorization", "X-Requested-With"],
};

app.use(cors(corsOptions));

app.use(express.json());

// Mount routes at both /api and / for compatibility with all frontend configs
app.use("/api", routes);
app.use("/", routes);

app.get("/health", (req, res) => {
  res.json({ status: "ok", message: "Behavioral AI Backend is running" });
});

// Error handler for CORS and general errors
app.use((err, req, res, next) => {
  if (err && err.message === "CORS request blocked by policy.") {
    return res.status(403).json({ error: "CORS request blocked by policy." });
  }
  next(err);
});

if (require.main === module) {
  app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
  });
}

module.exports = { app, isOriginAllowed };