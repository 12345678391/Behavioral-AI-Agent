process.env.LIVEKIT_API_KEY = "mock_key";
process.env.LIVEKIT_API_SECRET = "mock_secret_123456789012345678901234567890";
process.env.LIVEKIT_URL = "wss://mock.livekit.cloud";

const { app } = require("./server");

const server = app.listen(0, async () => {
  const port = server.address().port;
  let failures = 0;

  try {
    // Test 1: Invalid identity query parameter (expect 400)
    const res1 = await fetch(`http://localhost:${port}/api/token?identity=bad%20identity!`);
    const data1 = await res1.json();
    if (res1.status === 400 && data1.error) {
      console.log("PASS: Invalid identity rejected with 400");
    } else {
      console.error("FAIL: Invalid identity test", res1.status, data1);
      failures++;
    }

    // Test 2: Missing assessmentId (expect 400)
    const res2 = await fetch(`http://localhost:${port}/api/assessment-result`);
    const data2 = await res2.json();
    if (res2.status === 400 && data2.error) {
      console.log("PASS: Missing assessmentId rejected with 400");
    } else {
      console.error("FAIL: Missing assessmentId test", res2.status, data2);
      failures++;
    }

    // Test 3: Non-existent assessmentId with admin sync signature (expect 404) and unauthenticated (expect 401)
    const routesInitial = require("./routes");
    const nonexistentSig = routesInitial.computeSyncSignature("test-nonexistent-123", process.env.LIVEKIT_API_SECRET);
    const res3Unauth = await fetch(`http://localhost:${port}/api/assessment-result?assessmentId=test-nonexistent-123`);
    const res3 = await fetch(`http://localhost:${port}/api/assessment-result?assessmentId=test-nonexistent-123`, {
      headers: { "X-Assessment-Signature": nonexistentSig },
    });
    const data3 = await res3.json();
    if (res3Unauth.status === 401 && res3.status === 404 && data3.error) {
      console.log("PASS: Nonexistent assessmentId returned 404 for authorized sync and 401 for unauthenticated request");
    } else {
      console.error("FAIL: Nonexistent assessmentId test", { unauth: res3Unauth.status, auth: res3.status, data3 });
      failures++;
    }

    // Test 4: Token generation with unique dynamic room (expect 200)
    const res4 = await fetch(`http://localhost:${port}/api/token?identity=candidate-test`);
    const data4 = await res4.json();
    if (res4.status === 200 && data4.token && data4.room && data4.room.startsWith("behavior-room-")) {
      console.log("PASS: Token generated with dynamic room:", data4.room);
    } else {
      console.error("FAIL: Token generation test", res4.status, data4);
      failures++;
    }

    // Test 5: Verify dual-mounted /token route without /api prefix
    const res5 = await fetch(`http://localhost:${port}/token?identity=candidate-root`);
    const data5 = await res5.json();
    if (res5.status === 200 && data5.token) {
      console.log("PASS: Dual-mounted root route /token works");
    } else {
      console.error("FAIL: Root route test", res5.status, data5);
      failures++;
    }

    // Test 6: Production Vercel origin allowed with credentials
    const prodOrigin = "https://behavioral-ai-agent-src.vercel.app";
    const res6 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: prodOrigin },
    });
    const allowOrigin6 = res6.headers.get("access-control-allow-origin");
    const allowCreds6 = res6.headers.get("access-control-allow-credentials");
    if (res6.status === 200 && allowOrigin6 === prodOrigin && allowCreds6 === "true") {
      console.log("PASS: Production Vercel origin allowed with credentials");
    } else {
      console.error("FAIL: Production Vercel origin CORS test", res6.status, allowOrigin6, allowCreds6);
      failures++;
    }

    // Test 7: Production Vercel origin with trailing slash normalized and allowed
    const res7 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: `${prodOrigin}/` },
    });
    const allowOrigin7 = res7.headers.get("access-control-allow-origin");
    if (res7.status === 200 && (allowOrigin7 === prodOrigin || allowOrigin7 === `${prodOrigin}/`)) {
      console.log("PASS: Production Vercel origin with trailing slash accepted");
    } else {
      console.error("FAIL: Trailing slash origin CORS test", res7.status, allowOrigin7);
      failures++;
    }

    // Test 8: Current production deployment origin allowed with credentials
    const currentProdOrigin = "https://behavioral-ai-agent-i7so64hrb-behaviour-ai.vercel.app";
    const res8 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: currentProdOrigin },
    });
    const allowOrigin8 = res8.headers.get("access-control-allow-origin");
    const allowCreds8 = res8.headers.get("access-control-allow-credentials");
    if (res8.status === 200 && allowOrigin8 === currentProdOrigin && allowCreds8 === "true") {
      console.log("PASS: Current production deployment origin allowed with credentials");
    } else {
      console.error("FAIL: Current production deployment CORS test", res8.status, allowOrigin8, allowCreds8);
      failures++;
    }

    // Test 9: Current production deployment origin with trailing slash accepted
    const res9 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: `${currentProdOrigin}/` },
    });
    const allowOrigin9 = res9.headers.get("access-control-allow-origin");
    if (res9.status === 200 && (allowOrigin9 === currentProdOrigin || allowOrigin9 === `${currentProdOrigin}/`)) {
      console.log("PASS: Current production deployment with trailing slash accepted");
    } else {
      console.error("FAIL: Current production deployment trailing slash CORS test", res9.status, allowOrigin9);
      failures++;
    }

    // Test 10: Bare behavioral-ai-agent.vercel.app origin allowed
    const bareVercelOrigin = "https://behavioral-ai-agent.vercel.app";
    const res10 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: bareVercelOrigin },
    });
    const allowOrigin10 = res10.headers.get("access-control-allow-origin");
    if (res10.status === 200 && allowOrigin10 === bareVercelOrigin) {
      console.log("PASS: Bare behavioral-ai-agent.vercel.app origin allowed");
    } else {
      console.error("FAIL: Bare Vercel origin CORS test", res10.status, allowOrigin10);
      failures++;
    }

    // Test 11: Safe Vercel preview deployment origins allowed (both project patterns)
    const previewOrigins = [
      "https://behavioral-ai-agent-src-git-main-test.vercel.app",
      "https://behavioral-ai-agent-bmpbzda28-behaviour-ai.vercel.app",
      "https://behavioral-ai-agent-git-master-behaviour-ai.vercel.app",
      "https://behavioral-ai-agent-17oagipbu-behaviour-ai.vercel.app",
    ];
    for (const preview of previewOrigins) {
      const resPrev = await fetch(`http://localhost:${port}/health`, {
        headers: { Origin: preview },
      });
      const allowPrev = resPrev.headers.get("access-control-allow-origin");
      if (resPrev.status === 200 && allowPrev === preview) {
        console.log(`PASS: Vercel preview deployment allowed: ${preview}`);
      } else {
        console.error(`FAIL: Vercel preview CORS test for ${preview}`, resPrev.status, allowPrev);
        failures++;
      }
    }

    // Test 12: Localhost development origin allowed
    const localOrigin = "http://localhost:3000";
    const res12 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: localOrigin },
    });
    const allowOrigin12 = res12.headers.get("access-control-allow-origin");
    if (res12.status === 200 && allowOrigin12 === localOrigin) {
      console.log("PASS: Localhost dev origin allowed");
    } else {
      console.error("FAIL: Localhost CORS test", res12.status, allowOrigin12);
      failures++;
    }

    // Test 13: OPTIONS preflight response with methods and headers for current production origin
    const res13 = await fetch(`http://localhost:${port}/token`, {
      method: "OPTIONS",
      headers: {
        Origin: currentProdOrigin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Content-Type",
      },
    });
    const allowOrigin13 = res13.headers.get("access-control-allow-origin");
    const allowMethods13 = res13.headers.get("access-control-allow-methods");
    const allowCreds13 = res13.headers.get("access-control-allow-credentials");
    if (res13.status === 204 && allowOrigin13 === currentProdOrigin && allowMethods13 && allowCreds13 === "true") {
      console.log("PASS: Preflight OPTIONS request handled properly (HTTP 204) with credentials");
    } else {
      console.error("FAIL: OPTIONS preflight test", res13.status, allowOrigin13, allowMethods13, allowCreds13);
      failures++;
    }

    // Test 14: Preflight OPTIONS request for rejected origin rejected with HTTP 403
    const res14 = await fetch(`http://localhost:${port}/token`, {
      method: "OPTIONS",
      headers: {
        Origin: "https://unauthorized-evil-site.com",
        "Access-Control-Request-Method": "GET",
      },
    });
    if (res14.status === 403) {
      console.log("PASS: Preflight OPTIONS for unauthorized origin rejected with HTTP 403");
    } else {
      console.error("FAIL: Preflight OPTIONS rejected test", res14.status);
      failures++;
    }

    // Test 15: Unauthorized origin rejected by CORS policy on GET
    const res15 = await fetch(`http://localhost:${port}/token?identity=candidate-test`, {
      headers: { Origin: "https://unauthorized-evil-site.com" },
    });
    if (res15.status === 403) {
      console.log("PASS: Unauthorized origin rejected with HTTP 403");
    } else {
      console.error("FAIL: Unauthorized origin rejection test", res15.status);
      failures++;
    }

    // Test 16: Lookalike attacker domains rejected by CORS policy
    const lookalikes = [
      "https://evil-behavioral-ai-agent-src.vercel.app",
      "https://evil-behavioral-ai-agent.vercel.app",
      "https://behavioral-ai-agent.attacker.com",
      "https://behavioral-ai-agent.vercel.app.attacker.com",
      "https://unrelated-project.vercel.app",
    ];
    for (const lookalike of lookalikes) {
      const resLookalike = await fetch(`http://localhost:${port}/token?identity=candidate-test`, {
        headers: { Origin: lookalike },
      });
      if (resLookalike.status === 403) {
        console.log(`PASS: Lookalike/unrelated domain rejected with HTTP 403: ${lookalike}`);
      } else {
        console.error(`FAIL: Domain rejection test for ${lookalike}`, resLookalike.status);
        failures++;
      }
    }

    // Test 17: Explicit FRONTEND_ORIGIN dynamic environment variable support
    process.env.FRONTEND_ORIGIN = "https://custom-portal.example.com,https://another-client.org";
    const res17 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: "https://custom-portal.example.com" },
    });
    const allowOrigin17 = res17.headers.get("access-control-allow-origin");
    if (res17.status === 200 && allowOrigin17 === "https://custom-portal.example.com") {
      console.log("PASS: Explicit FRONTEND_ORIGIN origin allowed dynamically");
    } else {
      console.error("FAIL: FRONTEND_ORIGIN CORS test", res17.status, allowOrigin17);
      failures++;
    }

    // Test 18: LiveKit token generation with surrounding whitespace/newlines in credentials
    const createToken = require("./livekit");
    const origKey = process.env.LIVEKIT_API_KEY;
    const origSecret = process.env.LIVEKIT_API_SECRET;
    try {
      process.env.LIVEKIT_API_KEY = "  mock_key_with_spaces\n  ";
      process.env.LIVEKIT_API_SECRET = '"mock_secret_with_quotes_1234567890123456"';
      const tokenRes = await createToken("candidate-trim-test");
      if (tokenRes && tokenRes.token && tokenRes.room) {
        console.log("PASS: LiveKit token generation succeeds with dirty/whitespace credentials");
      } else {
        console.error("FAIL: LiveKit token credential trimming test failed");
        failures++;
      }
    } finally {
      process.env.LIVEKIT_API_KEY = origKey;
      process.env.LIVEKIT_API_SECRET = origSecret;
    }

    // Test 19: Authenticated POST /assessment-result and cached retrieval via GET /assessment-result
    const routes = require("./routes");
    const syncRoomId = "test-assessment-sync-room";
    const validSignature = routes.computeSyncSignature(syncRoomId, process.env.LIVEKIT_API_SECRET);
    const testAssessmentPayload = {
      assessment_id: syncRoomId,
      completed: true,
      final_analysis: {
        overall_score: 85,
        scores: { communication: 90, teamwork: 85 },
      },
    };
    const postRes = await fetch(`http://localhost:${port}/assessment-result?assessmentId=${syncRoomId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Assessment-Signature": validSignature,
      },
      body: JSON.stringify(testAssessmentPayload),
    });
    const postJson = await postRes.json();

    const getRes = await fetch(`http://localhost:${port}/assessment-result?assessmentId=${syncRoomId}`, {
      headers: { "X-Assessment-Signature": validSignature },
    });
    const getJson = await getRes.json();

    if (
      postRes.status === 200 &&
      postJson.success &&
      getRes.status === 200 &&
      getJson.final_analysis?.overall_score === 85
    ) {
      console.log("PASS: Authenticated POST and cached GET /assessment-result cross-container sync works");
    } else {
      console.error("FAIL: POST /assessment-result sync test failed", postRes.status, getRes.status);
      failures++;
    }

    // Test 20: POST /assessment-result rejects missing or invalid signature with 401
    const unauthRes = await fetch(`http://localhost:${port}/assessment-result?assessmentId=${syncRoomId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Assessment-Signature": "deadbeef0000",
      },
      body: JSON.stringify(testAssessmentPayload),
    });
    if (unauthRes.status === 401) {
      console.log("PASS: Unauthorized POST /assessment-result rejected with 401");
    } else {
      console.error("FAIL: Expected 401 for unauthorized POST /assessment-result, got", unauthRes.status);
      failures++;
    }

    // Test 21: POST /assessment-result rejects incomplete payload (final_analysis: null) with 400
    const incompleteRoomId = "test-incomplete-room";
    const incompleteSig = routes.computeSyncSignature(incompleteRoomId, process.env.LIVEKIT_API_SECRET);
    const incompleteRes = await fetch(`http://localhost:${port}/assessment-result?assessmentId=${incompleteRoomId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Assessment-Signature": incompleteSig,
      },
      body: JSON.stringify({
        assessment_id: incompleteRoomId,
        completed: false,
        final_analysis: null,
      }),
    });
    if (incompleteRes.status === 400) {
      console.log("PASS: Incomplete assessment payload rejected with 400");
    } else {
      console.error("FAIL: Expected 400 for incomplete assessment payload, got", incompleteRes.status);
      failures++;
    }

    // Test 22: Bounded cache enforces CACHE_MAX_ENTRIES and TTL eviction
    routes.assessmentResultsCache.clear();
    const maxEntries = routes.CACHE_MAX_ENTRIES;
    for (let i = 0; i < maxEntries + 15; i++) {
      routes.setCachedAssessmentResult(`room-cap-${i}`, testAssessmentPayload);
    }
    const oldestEvicted = routes.getCachedAssessmentResult("room-cap-0") === null;
    const newestRetained = routes.getCachedAssessmentResult(`room-cap-${maxEntries + 14}`) !== null;
    const sizeWithinCap = routes.assessmentResultsCache.size <= maxEntries;

    // Also verify TTL expiration
    routes.assessmentResultsCache.set("room-expired", {
      data: testAssessmentPayload,
      timestamp: Date.now() - routes.CACHE_TTL_MS - 1000,
    });
    const expiredEvicted = routes.getCachedAssessmentResult("room-expired") === null;

    if (oldestEvicted && newestRetained && sizeWithinCap && expiredEvicted) {
      console.log("PASS: assessmentResultsCache enforces max entry cap and TTL eviction");
    } else {
      console.error("FAIL: Cache bounds test failed", {
        oldestEvicted,
        newestRetained,
        sizeWithinCap,
        expiredEvicted,
        size: routes.assessmentResultsCache.size,
      });
      failures++;
    }

    // ============================================================
    // RECORDING INTEGRATION TESTS
    // ============================================================
    const recording = require("./recording");

    // Test 23: Invalid room rejected with 400 on recording endpoints
    const badStartRes = await fetch(`http://localhost:${port}/api/recording/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: "bad room!@#" }),
    });
    const badStopRes = await fetch(`http://localhost:${port}/api/recording/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: "bad room!@#" }),
    });
    const badStatusRes = await fetch(`http://localhost:${port}/api/recording/status?room=bad%20room!`);

    if (badStartRes.status === 400 && badStopRes.status === 400 && badStatusRes.status === 400) {
      console.log("PASS: Invalid room rejected with 400 across all recording endpoints");
    } else {
      console.error("FAIL: Invalid room validation failed", badStartRes.status, badStopRes.status, badStatusRes.status);
      failures++;
    }

    // Test 24: Recording start handles missing storage credentials gracefully
    recording.resetRecordingSessionsForTest();
    const noStorageRes = await fetch(`http://localhost:${port}/api/recording/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: "test-room-no-storage" }),
    });
    const noStorageData = await noStorageRes.json();
    if (noStorageData.status === "failed" && noStorageData.error && noStorageData.error.includes("storage")) {
      console.log("PASS: Recording start handles missing storage configuration gracefully");
    } else {
      console.error("FAIL: Missing storage handling test failed", noStorageData);
      failures++;
    }

    // Test 25: Mock Egress client start, stop, and status retrieval
    const mockClient = {
      startRoomCompositeEgress: async (room, output, opts) => {
        return { egressId: "egress-test-abc", status: 1 };
      },
      stopEgress: async (egressId) => {
        return {
          egressId,
          status: 3, // EGRESS_COMPLETE
          fileResults: [{ location: "https://my-bucket.s3.us-east-1.amazonaws.com/recordings/test.mp4" }],
        };
      },
      listEgress: async (req) => {
        return [{
          egressId: "egress-test-abc",
          status: 3,
          fileResults: [{ location: "https://my-bucket.s3.us-east-1.amazonaws.com/recordings/test.mp4" }],
        }];
      },
    };

    process.env.S3_KEY_ID = "test-key";
    process.env.S3_KEY_SECRET = "test-secret-never-expose";
    process.env.S3_BUCKET = "my-bucket";
    process.env.S3_REGION = "us-east-1";

    recording.setEgressClientForTest(mockClient);

    const testRoom = "test-room-rec-123";
    const startRes = await fetch(`http://localhost:${port}/api/recording/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: testRoom }),
    });
    const startData = await startRes.json();

    if (startData.success === true && startData.status === "active" && startData.egressId === "egress-test-abc") {
      console.log("PASS: Server-side recording start succeeds with mock EgressClient");
    } else {
      console.error("FAIL: Recording start with mock client failed", startData);
      failures++;
    }

    // Test 26: Secrets are NEVER exposed in responses or state
    const serializedStart = JSON.stringify(startData);
    if (!serializedStart.includes("test-secret-never-expose") && !serializedStart.includes("mock_secret")) {
      console.log("PASS: LiveKit credentials and storage secrets are strictly preserved server-side");
    } else {
      console.error("FAIL: Secret exposure detected in startData", serializedStart);
      failures++;
    }

    // Test 27: Recording stop and status query with capability token authorization
    const testRecToken = "test-archive-token-rec-1234567890abcdef12345678";
    routes.registerRoomCapabilityToken(testRoom, testRecToken);

    const stopRes = await fetch(`http://localhost:${port}/api/recording/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: testRoom }),
    });
    const stopData = await stopRes.json();

    const statusRes = await fetch(`http://localhost:${port}/api/recording/status?room=${testRoom}`, {
      headers: { Authorization: `Bearer ${testRecToken}` },
    });
    const statusData = await statusRes.json();

    if (
      stopData.success === true &&
      stopData.videoUrl === null &&
      statusData.success === true &&
      statusData.status === "completed" &&
      typeof statusData.videoUrl === "string" &&
      statusData.videoUrl.startsWith("https://my-bucket.s3.us-east-1.amazonaws.com/recordings/") &&
      statusData.videoUrl.includes("X-Amz-Algorithm=AWS4-HMAC-SHA256") &&
      statusData.videoUrl.includes("X-Amz-Signature=")
    ) {
      console.log("PASS: Recording status returns completed with verified playback URL:", statusData.videoUrl);
    } else {
      console.error("FAIL: Recording status query failed", { stopData, statusData });
      failures++;
    }

    // Test 28: Verify web/server.cjs (port 3001 target) mounts recording & token endpoints at both / and /api
    const { app: webApp } = require("../web/server.cjs");
    const webServer = webApp.listen(0);
    const webPort = webServer.address().port;

    const webHealth = await fetch(`http://localhost:${webPort}/health`);
    const webHealthData = await webHealth.json();
    const webTokenRoot = await fetch(`http://localhost:${webPort}/token?identity=web-root-test`);
    const webTokenApi = await fetch(`http://localhost:${webPort}/api/token?identity=web-api-test`);
    const webRecStartRoot = await fetch(`http://localhost:${webPort}/recording/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: "web-test-room" }),
    });
    const webRecStartApi = await fetch(`http://localhost:${webPort}/api/recording/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: "web-test-room-2" }),
    });

    if (
      webHealth.status === 200 &&
      webHealthData.status === "ok" &&
      webTokenRoot.status === 200 &&
      webTokenApi.status === 200 &&
      webRecStartRoot.status === 200 &&
      webRecStartApi.status === 200
    ) {
      console.log("PASS: web/server.cjs successfully mounts token and recording routes at / and /api");
    } else {
      console.error("FAIL: web/server.cjs route verification failed", {
        health: webHealth.status,
        tokenRoot: webTokenRoot.status,
        tokenApi: webTokenApi.status,
        recRoot: webRecStartRoot.status,
        recApi: webRecStartApi.status,
      });
      failures++;
    }

    await new Promise((resolve) => webServer.close(resolve));

    // Test 29: LiveKit Egress without valid egressId fails without fabricating fake ID
    const failingClient = {
      startRoomCompositeEgress: async () => ({ egressId: null }),
    };
    recording.setEgressClientForTest(failingClient);
    const failRes = await fetch(`http://localhost:${port}/api/recording/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: "test-room-no-fake-id" }),
    });
    const failData = await failRes.json();
    if (failData.success === false && failData.status === "failed" && !failData.egressId) {
      console.log("PASS: Egress start fails safely without generating fabricated egress ID");
    } else {
      console.error("FAIL: Egress start should have rejected missing egressId", failData);
      failures++;
    }

    // ============================================================
    // CLOUDFLARE R2 RECORDING PLAYBACK URL TESTS
    // ============================================================
    const originalR2Env = {
      RECORDING_BASE_URL: process.env.RECORDING_BASE_URL,
      S3_PUBLIC_BASE_URL: process.env.S3_PUBLIC_BASE_URL,
      STORAGE_PUBLIC_URL: process.env.STORAGE_PUBLIC_URL,
      hasRecordingBaseUrl: Object.prototype.hasOwnProperty.call(process.env, "RECORDING_BASE_URL"),
      hasS3PublicBaseUrl: Object.prototype.hasOwnProperty.call(process.env, "S3_PUBLIC_BASE_URL"),
      hasStoragePublicUrl: Object.prototype.hasOwnProperty.call(process.env, "STORAGE_PUBLIC_URL"),
    };

    function restoreR2Env() {
      if (originalR2Env.hasRecordingBaseUrl) {
        process.env.RECORDING_BASE_URL = originalR2Env.RECORDING_BASE_URL;
      } else {
        delete process.env.RECORDING_BASE_URL;
      }
      if (originalR2Env.hasS3PublicBaseUrl) {
        process.env.S3_PUBLIC_BASE_URL = originalR2Env.S3_PUBLIC_BASE_URL;
      } else {
        delete process.env.S3_PUBLIC_BASE_URL;
      }
      if (originalR2Env.hasStoragePublicUrl) {
        process.env.STORAGE_PUBLIC_URL = originalR2Env.STORAGE_PUBLIC_URL;
      } else {
        delete process.env.STORAGE_PUBLIC_URL;
      }
    }

    try {
      // Test 30: Configured public base URL takes priority over private R2 S3 endpoint
      const r2StorageConfig = {
        type: "s3",
        configured: true,
        bucket: "interview-recordings",
        region: "auto",
        endpoint: "https://my-account-id.r2.cloudflarestorage.com",
      };
      const r2Filepath = "recordings/candidate-room-123.mp4";
      const r2PrivateEgressResults = [
        { location: "https://my-account-id.r2.cloudflarestorage.com/interview-recordings/recordings/candidate-room-123.mp4" }
      ];

      process.env.RECORDING_BASE_URL = "https://pub-abcdef123456.r2.dev";
      const r2PublicUrl = recording.computePlaybackUrl(
        r2StorageConfig,
        r2Filepath,
        r2PrivateEgressResults,
        null
      );

      if (r2PublicUrl === "https://pub-abcdef123456.r2.dev/recordings/candidate-room-123.mp4") {
        console.log("PASS: RECORDING_BASE_URL takes priority over private R2 S3 endpoint");
      } else {
        console.error("FAIL: RECORDING_BASE_URL priority test failed:", r2PublicUrl);
        failures++;
      }

      // Test 31: Environment variable priority hierarchy (RECORDING_BASE_URL > S3_PUBLIC_BASE_URL > STORAGE_PUBLIC_URL)
      process.env.S3_PUBLIC_BASE_URL = "https://s3-public.example.com";
      process.env.STORAGE_PUBLIC_URL = "https://storage-public.example.com";
      const hierarchyUrl1 = recording.computePlaybackUrl(r2StorageConfig, r2Filepath, r2PrivateEgressResults, null);

      delete process.env.RECORDING_BASE_URL;
      const hierarchyUrl2 = recording.computePlaybackUrl(r2StorageConfig, r2Filepath, r2PrivateEgressResults, null);

      delete process.env.S3_PUBLIC_BASE_URL;
      const hierarchyUrl3 = recording.computePlaybackUrl(r2StorageConfig, r2Filepath, r2PrivateEgressResults, null);

      if (
        hierarchyUrl1 === "https://pub-abcdef123456.r2.dev/recordings/candidate-room-123.mp4" &&
        hierarchyUrl2 === "https://s3-public.example.com/recordings/candidate-room-123.mp4" &&
        hierarchyUrl3 === "https://storage-public.example.com/recordings/candidate-room-123.mp4"
      ) {
        console.log("PASS: Public base URL priority hierarchy respected (RECORDING_BASE_URL > S3_PUBLIC_BASE_URL > STORAGE_PUBLIC_URL)");
      } else {
        console.error("FAIL: Public base URL hierarchy test failed", { hierarchyUrl1, hierarchyUrl2, hierarchyUrl3 });
        failures++;
      }

      // Test 32: URL formatting handles trailing and leading slashes without duplicate slashes
      process.env.RECORDING_BASE_URL = "https://recordings.example.com///";
      const slashCleanUrl = recording.computePlaybackUrl(
        r2StorageConfig,
        "///recordings/nested/session-video.mp4",
        r2PrivateEgressResults,
        null
      );
      if (slashCleanUrl === "https://recordings.example.com/recordings/nested/session-video.mp4") {
        console.log("PASS: URL formatting normalizes slashes without duplicate slashes");
      } else {
        console.error("FAIL: Slash formatting test failed:", slashCleanUrl);
        failures++;
      }

      // Test 33: Existing fallback behavior preserved when no public base URL is configured
      delete process.env.RECORDING_BASE_URL;
      delete process.env.S3_PUBLIC_BASE_URL;
      delete process.env.STORAGE_PUBLIC_URL;

      const fallbackWithEgressResult = recording.computePlaybackUrl(
        r2StorageConfig,
        r2Filepath,
        [{ location: "https://my-bucket.s3.us-east-1.amazonaws.com/test.mp4" }],
        null
      );
      const fallbackWithoutEgressResult = recording.computePlaybackUrl(
        r2StorageConfig,
        r2Filepath,
        [],
        null
      );

      if (
        fallbackWithEgressResult === "https://my-bucket.s3.us-east-1.amazonaws.com/test.mp4" &&
        fallbackWithoutEgressResult === "https://my-account-id.r2.cloudflarestorage.com/interview-recordings/recordings/candidate-room-123.mp4"
      ) {
        console.log("PASS: Fallback behavior preserved when no public base URL is configured");
      } else {
        console.error("FAIL: Fallback behavior test failed", { fallbackWithEgressResult, fallbackWithoutEgressResult });
        failures++;
      }
    } finally {
      restoreR2Env();
    }

    // ============================================================
    // SECURE PRIVATE CLOUDFLARE R2 RECORDING & CAPABILITY TOKEN TESTS
    // ============================================================
    const crypto = require("crypto");
    const originalSecureEnv = {
      S3_KEY_ID: process.env.S3_KEY_ID,
      S3_KEY_SECRET: process.env.S3_KEY_SECRET,
      S3_BUCKET: process.env.S3_BUCKET,
      S3_REGION: process.env.S3_REGION,
      S3_ENDPOINT: process.env.S3_ENDPOINT,
      RECORDING_BASE_URL: process.env.RECORDING_BASE_URL,
      S3_PUBLIC_BASE_URL: process.env.S3_PUBLIC_BASE_URL,
      STORAGE_PUBLIC_URL: process.env.STORAGE_PUBLIC_URL,
    };

    try {
      // Set private S3/R2 storage environment
      process.env.S3_KEY_ID = "mock-r2-access-key-id";
      process.env.S3_KEY_SECRET = "mock-r2-secret-access-key-12345678";
      process.env.S3_BUCKET = "interview-recordings";
      process.env.S3_REGION = "auto";
      process.env.S3_ENDPOINT = "https://mock-account.r2.cloudflarestorage.com";
      delete process.env.RECORDING_BASE_URL;
      delete process.env.S3_PUBLIC_BASE_URL;
      delete process.env.STORAGE_PUBLIC_URL;

      // Test 34: Token generation returns 256-bit archiveToken and stores only SHA-256 hash
      const tokenRes = await fetch(`http://localhost:${port}/api/token?identity=candidate-secure-1`);
      const tokenData = await tokenRes.json();
      const secureRoom = tokenData.room;
      const archiveToken = tokenData.archiveToken;

      const registeredAuth = routes.roomAuthRegistry.get(secureRoom);
      const expectedTokenHash = crypto.createHash("sha256").update(archiveToken).digest("hex");

      if (
        tokenRes.status === 200 &&
        typeof archiveToken === "string" &&
        archiveToken.length === 64 &&
        registeredAuth &&
        registeredAuth.archiveTokenHash === expectedTokenHash &&
        registeredAuth.archiveTokenHash !== archiveToken
      ) {
        console.log("PASS: Token generation returns 256-bit archiveToken and stores only its SHA-256 hash");
      } else {
        console.error("FAIL: Capability token generation/storage test failed", { tokenData, registeredAuth });
        failures++;
      }

      // Simulate completed recording session for secureRoom
      recording.setRecordingSession(secureRoom, {
        roomName: secureRoom,
        status: "completed",
        egressId: "egress-secure-1",
        filepath: `recordings/${secureRoom}.mp4`,
        videoUrl: `https://mock-account.r2.cloudflarestorage.com/interview-recordings/recordings/${secureRoom}.mp4`,
        error: null,
        startedAt: Date.now() - 60000,
        endedAt: Date.now(),
        storageType: "s3",
      });

      // Test 35: Protected recording status rejects unauthenticated request with 401
      const unauthStatusRes = await fetch(`http://localhost:${port}/api/recording/status?room=${secureRoom}`);
      const unauthStatusData = await unauthStatusRes.json();
      if (unauthStatusRes.status === 401 && unauthStatusData.status === "failed") {
        console.log("PASS: Unauthenticated request to protected recording status rejected with 401");
      } else {
        console.error("FAIL: Expected 401 for unauthenticated recording status, got", unauthStatusRes.status, unauthStatusData);
        failures++;
      }

      // Test 36: Protected recording status rejects invalid/cross-room capability token with 403
      const badTokenStatusRes = await fetch(`http://localhost:${port}/api/recording/status?room=${secureRoom}`, {
        headers: { Authorization: "Bearer wrong-or-other-room-token-abcdef123456" },
      });
      const badTokenStatusData = await badTokenStatusRes.json();
      if (badTokenStatusRes.status === 403 && badTokenStatusData.status === "failed") {
        console.log("PASS: Invalid capability token on recording status rejected with 403");
      } else {
        console.error("FAIL: Expected 403 for invalid capability token, got", badTokenStatusRes.status, badTokenStatusData);
        failures++;
      }

      // Test 37: Authenticated request returns short-lived pre-signed SigV4 URL
      const authStatusRes = await fetch(`http://localhost:${port}/api/recording/status?room=${secureRoom}`, {
        headers: { Authorization: `Bearer ${archiveToken}` },
      });
      const authStatusData = await authStatusRes.json();
      const signedUrl = authStatusData.videoUrl;
      const parsedSignedUrl = signedUrl ? new URL(signedUrl) : null;

      if (
        authStatusRes.status === 200 &&
        authStatusData.status === "completed" &&
        parsedSignedUrl &&
        parsedSignedUrl.searchParams.get("X-Amz-Algorithm") === "AWS4-HMAC-SHA256" &&
        parsedSignedUrl.searchParams.get("X-Amz-Credential")?.startsWith("mock-r2-access-key-id") &&
        parsedSignedUrl.searchParams.get("X-Amz-Expires") === "900" &&
        parsedSignedUrl.searchParams.get("X-Amz-Signature")
      ) {
        console.log("PASS: Authenticated request returns valid short-lived AWS SigV4 pre-signed URL");
      } else {
        console.error("FAIL: SigV4 presigned URL generation test failed", { authStatusData, signedUrl });
        failures++;
      }

      // Test 38: Pre-signed URL signs ONLY host to preserve HTML5 video seeking & HTTP Range requests
      const signedHeaders = parsedSignedUrl?.searchParams.get("X-Amz-SignedHeaders");
      if (signedHeaders === "host") {
        console.log("PASS: Pre-signed URL signs only host, preserving HTTP Range requests for video seeking/scrubbing");
      } else {
        console.error("FAIL: Signed headers check failed; expected 'host', got:", signedHeaders);
        failures++;
      }

      // Test 39: Per-room revocation immediately blocks revoked room (403) while Room B remains accessible
      const tokenResB = await fetch(`http://localhost:${port}/api/token?identity=candidate-secure-2`);
      const tokenDataB = await tokenResB.json();
      const secureRoomB = tokenDataB.room;
      const archiveTokenB = tokenDataB.archiveToken;

      recording.setRecordingSession(secureRoomB, {
        roomName: secureRoomB,
        status: "completed",
        egressId: "egress-secure-2",
        filepath: `recordings/${secureRoomB}.mp4`,
        videoUrl: `https://mock-account.r2.cloudflarestorage.com/interview-recordings/recordings/${secureRoomB}.mp4`,
        error: null,
      });

      // Test 39: Per-room revocation strictly requires Admin HMAC signature (fails closed for candidate and anonymous callers)
      // Anonymous revocation attempt -> expect 401
      const anonRevoke = await fetch(`http://localhost:${port}/api/recording/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room: secureRoom }),
      });

      // Candidate capability token revocation attempt -> expect 401 (signature required)
      const candRevoke = await fetch(`http://localhost:${port}/api/recording/revoke`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${archiveToken}`,
        },
        body: JSON.stringify({ room: secureRoom }),
      });

      // Invalid admin signature -> expect 403
      const badSigRevoke = await fetch(`http://localhost:${port}/api/recording/revoke`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Signature": "invalid-deadbeef-signature-0000",
        },
        body: JSON.stringify({ room: secureRoom }),
      });

      // Authorized Admin revocation with valid HMAC-SHA256 signature
      const adminSig = routes.computeSyncSignature(secureRoom, process.env.LIVEKIT_API_SECRET);
      const revokeRes = await fetch(`http://localhost:${port}/api/recording/revoke`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Signature": adminSig,
        },
        body: JSON.stringify({ room: secureRoom }),
      });
      const revokeData = await revokeRes.json();

      // Access Room A with valid token (should now fail with 403)
      const postRevokeStatusResA = await fetch(`http://localhost:${port}/api/recording/status?room=${secureRoom}`, {
        headers: { Authorization: `Bearer ${archiveToken}` },
      });
      const postRevokeStatusDataA = await postRevokeStatusResA.json();

      // Access Room B with valid token (should succeed with 200)
      const statusResB = await fetch(`http://localhost:${port}/api/recording/status?room=${secureRoomB}`, {
        headers: { Authorization: `Bearer ${archiveTokenB}` },
      });
      const statusDataB = await statusResB.json();

      if (
        anonRevoke.status === 401 &&
        candRevoke.status === 401 &&
        badSigRevoke.status === 403 &&
        revokeRes.status === 200 &&
        revokeData.revoked === true &&
        postRevokeStatusResA.status === 403 &&
        postRevokeStatusDataA.error?.includes("revoked") &&
        statusResB.status === 200 &&
        statusDataB.status === "completed"
      ) {
        console.log("PASS: Per-room revocation strictly enforces Admin HMAC signature and blocks revoked session while others remain unaffected");
      } else {
        console.error("FAIL: Per-room revocation test failed", {
          anonStatus: anonRevoke.status,
          candStatus: candRevoke.status,
          badSigStatus: badSigRevoke.status,
          revokeStatus: revokeRes.status,
          revokeData,
          postRevokeStatusA: postRevokeStatusResA.status,
          postRevokeStatusDataA,
          statusB: statusResB.status,
        });
        failures++;
      }

      // Test 40: Protected assessment-result retrieval enforces capability token and omits archiveTokenHash
      const secureAssessmentId = `assessment-${Date.now()}`;
      const secureAssessmentSig = routes.computeSyncSignature(secureAssessmentId, process.env.LIVEKIT_API_SECRET);
      const secureAssessmentToken = crypto.randomBytes(32).toString("hex");
      routes.registerRoomCapabilityToken(secureAssessmentId, secureAssessmentToken);

      const postAssessmentRes = await fetch(`http://localhost:${port}/api/assessment-result?assessmentId=${secureAssessmentId}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Assessment-Signature": secureAssessmentSig,
        },
        body: JSON.stringify({
          assessment_id: secureAssessmentId,
          completed: true,
          final_analysis: {
            overall_score: 95,
            scores: { technical: 95 },
          },
        }),
      });

      // Query without token -> 401
      const unauthAssessRes = await fetch(`http://localhost:${port}/api/assessment-result?assessmentId=${secureAssessmentId}`);
      // Query with token -> 200 and archiveTokenHash omitted from response
      const authAssessRes = await fetch(`http://localhost:${port}/api/assessment-result?assessmentId=${secureAssessmentId}`, {
        headers: { Authorization: `Bearer ${secureAssessmentToken}` },
      });
      const authAssessData = await authAssessRes.json();

      if (
        postAssessmentRes.status === 200 &&
        unauthAssessRes.status === 401 &&
        authAssessRes.status === 200 &&
        authAssessData.final_analysis?.overall_score === 95 &&
        authAssessData.archiveTokenHash === undefined
      ) {
        console.log("PASS: Assessment result retrieval enforces capability token and omits token hash from response");
      } else {
        console.error("FAIL: Protected assessment result retrieval test failed", {
          unauthStatus: unauthAssessRes.status,
          authStatus: authAssessRes.status,
          authAssessData,
        });
        failures++;
      }

      // Test 41: Immediate disk persistence and simulated process restart rehydration
      const restartRoom = `room-restart-${Date.now()}`;
      const restartIdentity = `candidate-restart-${Date.now()}`;
      const tokenRestartRes = await fetch(`http://localhost:${port}/api/token?identity=${restartIdentity}&room=${restartRoom}`);
      const tokenRestartData = await tokenRestartRes.json();
      const restartToken = tokenRestartData.archiveToken;
      const expectedRestartHash = crypto.createHash("sha256").update(restartToken).digest("hex");

      const fs = require("fs");
      const path = require("path");
      const assessmentsDir = path.join(__dirname, "..", "agent", "assessments");
      const restartFilePath = path.join(assessmentsDir, `${restartRoom}.json`);
      const diskFileContentBefore = JSON.parse(fs.readFileSync(restartFilePath, "utf-8"));

      // Simulate process restart: wipe in-memory registries completely
      routes.roomAuthRegistry.clear();
      routes.assessmentResultsCache.clear();

      // Verify getRoomAuthRecord rehydrates from disk
      const rehydratedRecord = routes.getRoomAuthRecord(restartRoom);

      // Set recording session to simulate completed interview on restarted server
      recording.setRecordingSession(restartRoom, {
        roomName: restartRoom,
        status: "completed",
        egressId: "egress-restart-1",
        filepath: `recordings/${restartRoom}.mp4`,
        videoUrl: `https://mock-account.r2.cloudflarestorage.com/interview-recordings/recordings/${restartRoom}.mp4`,
        error: null,
      });

      const rehydratedStatusRes = await fetch(`http://localhost:${port}/api/recording/status?room=${restartRoom}`, {
        headers: { Authorization: `Bearer ${restartToken}` },
      });
      const rehydratedStatusData = await rehydratedStatusRes.json();

      if (
        diskFileContentBefore.archiveTokenHash === expectedRestartHash &&
        rehydratedRecord &&
        rehydratedRecord.archiveTokenHash === expectedRestartHash &&
        rehydratedStatusRes.status === 200 &&
        rehydratedStatusData.status === "completed"
      ) {
        console.log("PASS: Capability token persisted to disk immediately and survives simulated process restart");
      } else {
        console.error("FAIL: Immediate disk persistence / restart rehydration test failed", {
          diskFileContentBefore,
          rehydratedRecord,
          rehydratedStatusData,
        });
        failures++;
      }

      // Test 42: Fail-closed access rejects missing authorization records
      const ghostRoom = `ghost-room-unregistered-${Date.now()}`;
      const ghostStatusResNoToken = await fetch(`http://localhost:${port}/api/recording/status?room=${ghostRoom}`);
      const ghostStatusResWithToken = await fetch(`http://localhost:${port}/api/recording/status?room=${ghostRoom}`, {
        headers: { Authorization: "Bearer some-arbitrary-token-123456" },
      });
      const ghostAssessRes = await fetch(`http://localhost:${port}/api/assessment-result?assessmentId=${ghostRoom}`);

      if (
        ghostStatusResNoToken.status === 401 &&
        ghostStatusResWithToken.status === 401 &&
        ghostAssessRes.status === 401
      ) {
        console.log("PASS: Fail-closed security verified: missing authorization records strictly rejected with 401");
      } else {
        console.error("FAIL: Fail-closed test failed", {
          noToken: ghostStatusResNoToken.status,
          withToken: ghostStatusResWithToken.status,
          assess: ghostAssessRes.status,
        });
        failures++;
      }

      // Test 43: Anti-overwrite on reconnect and full assessment data preservation
      const preserveRoom = `room-preserve-${Date.now()}`;
      const preserveToken = crypto.randomBytes(32).toString("hex");
      const preserveFilePath = path.join(assessmentsDir, `${preserveRoom}.json`);

      // Write full assessment with detailed fields first
      const fullAssessmentData = {
        assessment_id: preserveRoom,
        completed: true,
        transcript: [
          { speaker: "interviewer", text: "Welcome to your interview." },
          { speaker: "candidate", text: "Thank you, glad to be here." },
        ],
        final_analysis: {
          overall_score: 89,
          scores: { technical: 90, communication: 88 },
          strengths: ["Strong problem solving"],
          areas_for_improvement: ["Elaborate on trade-offs"],
          behavioral_summary: "Great candidate.",
        },
        recording: {
          status: "completed",
          filepath: `recordings/${preserveRoom}.mp4`,
          videoUrl: `https://mock-account.r2.cloudflarestorage.com/interview-recordings/recordings/${preserveRoom}.mp4`,
        },
      };
      fs.writeFileSync(preserveFilePath, JSON.stringify(fullAssessmentData, null, 2), "utf-8");

      // Register capability token (persists hash without clobbering existing assessment fields)
      routes.registerRoomCapabilityToken(preserveRoom, preserveToken);

      // Attempt reconnect without token -> rejected with 401
      const unauthReconnect = await fetch(`http://localhost:${port}/api/token?room=${preserveRoom}`);

      // Attempt reconnect with wrong token -> rejected with 403
      const badTokenReconnect = await fetch(`http://localhost:${port}/api/token?room=${preserveRoom}`, {
        headers: { Authorization: "Bearer wrong-candidate-token-12345" },
      });

      // Authorized reconnect with correct token -> preserves token and returns fresh LiveKit JWT
      const goodReconnect = await fetch(`http://localhost:${port}/api/token?room=${preserveRoom}`, {
        headers: { Authorization: `Bearer ${preserveToken}` },
      });
      const goodReconnectData = await goodReconnect.json();

      // Read disk file to ensure transcript, scores, and final_analysis were NOT wiped
      const diskAfterReconnect = JSON.parse(fs.readFileSync(preserveFilePath, "utf-8"));

      if (
        unauthReconnect.status === 401 &&
        badTokenReconnect.status === 403 &&
        goodReconnect.status === 200 &&
        goodReconnectData.archiveToken === preserveToken &&
        goodReconnectData.reconnected === true &&
        diskAfterReconnect.transcript?.length === 2 &&
        diskAfterReconnect.final_analysis?.overall_score === 89 &&
        diskAfterReconnect.recording?.filepath === `recordings/${preserveRoom}.mp4`
      ) {
        console.log("PASS: Anti-overwrite protects capability token on reconnect while preserving all existing assessment fields");
      } else {
        console.error("FAIL: Anti-overwrite and data preservation test failed", {
          unauth: unauthReconnect.status,
          badToken: badTokenReconnect.status,
          goodStatus: goodReconnect.status,
          goodData: goodReconnectData,
          diskAfterReconnect,
        });
        failures++;
      }

      // Test 44: AWS S3 standard environment aliases and private bucket configuration
      const origS3Env = {
        AWS_ACCESS_KEY_ID: process.env.AWS_ACCESS_KEY_ID,
        AWS_SECRET_ACCESS_KEY: process.env.AWS_SECRET_ACCESS_KEY,
        AWS_S3_BUCKET: process.env.AWS_S3_BUCKET,
        AWS_DEFAULT_REGION: process.env.AWS_DEFAULT_REGION,
        S3_KEY_ID: process.env.S3_KEY_ID,
        S3_KEY_SECRET: process.env.S3_KEY_SECRET,
        S3_BUCKET: process.env.S3_BUCKET,
        S3_REGION: process.env.S3_REGION,
        S3_ENDPOINT: process.env.S3_ENDPOINT,
      };

      try {
        delete process.env.S3_KEY_ID;
        delete process.env.S3_KEY_SECRET;
        delete process.env.S3_BUCKET;
        delete process.env.S3_REGION;
        delete process.env.S3_ENDPOINT;

        process.env.AWS_ACCESS_KEY_ID = "AKIA_TEST_PRIVATE_KEY_ID";
        process.env.AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY";
        process.env.AWS_S3_BUCKET = "private-interview-recordings";
        process.env.AWS_DEFAULT_REGION = "us-west-2";

        const awsStorageConfig = recording.getStorageConfig();
        const awsPresignedUrl = recording.generatePresignedPlaybackUrl(
          awsStorageConfig,
          "recordings/interview-session-123.mp4",
          900,
          new Date("2026-10-19T10:00:00Z")
        );

        if (
          awsStorageConfig.configured === true &&
          awsStorageConfig.type === "s3" &&
          awsStorageConfig.bucket === "private-interview-recordings" &&
          awsStorageConfig.region === "us-west-2" &&
          awsPresignedUrl &&
          awsPresignedUrl.startsWith("https://private-interview-recordings.s3.us-west-2.amazonaws.com/recordings/interview-session-123.mp4?") &&
          awsPresignedUrl.includes("X-Amz-Algorithm=AWS4-HMAC-SHA256") &&
          awsPresignedUrl.includes("X-Amz-Credential=AKIA_TEST_PRIVATE_KEY_ID%2F20261019%2Fus-west-2%2Fs3%2Faws4_request") &&
          awsPresignedUrl.includes("X-Amz-Signature=")
        ) {
          console.log("PASS: AWS S3 standard environment variables configure secure private S3 storage with valid SigV4 presigned URL");
        } else {
          console.error("FAIL: AWS S3 standard environment variables test failed", { awsStorageConfig, awsPresignedUrl });
          failures++;
        }

        // Test 45: AWS temporary session credentials (AWS_SESSION_TOKEN) in SigV4 URL
        process.env.AWS_SESSION_TOKEN = "AQoDYXdzEJr1EXAMPLETOKEN1234567890";
        const tempAwsStorageConfig = recording.getStorageConfig();
        const tempPresignedUrl = recording.generatePresignedPlaybackUrl(
          tempAwsStorageConfig,
          "recordings/interview-session-123.mp4",
          900,
          new Date("2026-10-19T10:00:00Z")
        );

        if (
          tempAwsStorageConfig.sessionToken === "AQoDYXdzEJr1EXAMPLETOKEN1234567890" &&
          tempAwsStorageConfig.uploadObject?.sessionToken === "AQoDYXdzEJr1EXAMPLETOKEN1234567890" &&
          tempPresignedUrl &&
          tempPresignedUrl.includes("X-Amz-Security-Token=AQoDYXdzEJr1EXAMPLETOKEN1234567890")
        ) {
          console.log("PASS: AWS temporary credentials (sessionToken) supported and included in SigV4 presigned URL");
        } else {
          console.error("FAIL: AWS session token test failed", { tempAwsStorageConfig, tempPresignedUrl });
          failures++;
        }
      } finally {
        delete process.env.AWS_SESSION_TOKEN;
        for (const [k, v] of Object.entries(origS3Env)) {
          if (v === undefined) delete process.env[k];
          else process.env[k] = v;
        }
      }

      // Test 46: Recording metadata preservation from LiveKit Egress fileResults
      const metaRoom = `meta-rec-room-${Date.now()}`;
      const metaToken = "meta-archive-token-1234567890abcdef12345678";
      routes.registerRoomCapabilityToken(metaRoom, metaToken);

      recording.setRecordingSession(metaRoom, {
        roomName: metaRoom,
        status: "active",
        egressId: "egress-meta-456",
        filepath: `recordings/${metaRoom}.mp4`,
        videoUrl: null,
        error: null,
        startedAt: Date.now() - 60000,
        endedAt: null,
        storageType: "s3",
      });

      // Mock EgressClient returning complete status with fileResults metadata
      const mockMetaClient = {
        stopEgress: async () => ({
          status: 3, // EGRESS_COMPLETE
          fileResults: [
            {
              filename: `recordings/${metaRoom}.mp4`,
              size: 15485760n, // ~14.7 MB BigInt
              duration: 185000000000n, // 185 seconds in nanoseconds
              location: `https://private-interview-recordings.s3.us-west-2.amazonaws.com/recordings/${metaRoom}.mp4`,
            },
          ],
        }),
        listEgress: async () => [],
      };
      recording.setEgressClientForTest(mockMetaClient);

      // 1. Verify unauthenticated POST /recording/stop returns videoUrl: null
      const unauthStopRes = await fetch(`http://localhost:${port}/api/recording/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room: metaRoom }),
      });
      const unauthStopData = await unauthStopRes.json();

      // 2. Verify invalid-token POST /recording/stop returns videoUrl: null
      const invalidTokenStopRes = await fetch(`http://localhost:${port}/api/recording/stop`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer invalid-archive-token-99999",
        },
        body: JSON.stringify({ room: metaRoom }),
      });
      const invalidTokenStopData = await invalidTokenStopRes.json();

      // 3. Verify authenticated POST /recording/stop returns presigned videoUrl and normalized duration
      const metaStopRes = await fetch(`http://localhost:${port}/api/recording/stop`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${metaToken}`,
        },
        body: JSON.stringify({ room: metaRoom }),
      });
      const metaStopData = await metaStopRes.json();

      const metaStatusRes = await fetch(`http://localhost:${port}/api/recording/status?room=${metaRoom}`, {
        headers: { Authorization: `Bearer ${metaToken}` },
      });
      const metaStatusData = await metaStatusRes.json();

      recording.setEgressClientForTest(null);

      if (
        unauthStopData.success === true &&
        unauthStopData.videoUrl === null &&
        invalidTokenStopData.success === true &&
        invalidTokenStopData.videoUrl === null &&
        metaStopData.status === "completed" &&
        metaStopData.videoUrl &&
        metaStopData.videoUrl.includes("X-Amz-Signature=") &&
        metaStopData.size === 15485760 &&
        metaStopData.duration === 185 &&
        metaStopData.duration_seconds === 185 &&
        metaStatusData.status === "completed" &&
        metaStatusData.size === 15485760 &&
        metaStatusData.duration === 185 &&
        metaStatusData.duration_seconds === 185 &&
        metaStatusData.videoUrl &&
        metaStatusData.videoUrl.includes("X-Amz-Signature=")
      ) {
        console.log("PASS: Recording stop protects videoUrl (null for unauth/invalid, presigned for auth) and normalizes duration to seconds");
      } else {
        console.error("FAIL: Recording metadata preservation test failed", { unauthStopData, invalidTokenStopData, metaStopData, metaStatusData });
        failures++;
      }

    } finally {
      // Restore original environment
      for (const [key, val] of Object.entries(originalSecureEnv)) {
        if (val === undefined) {
          delete process.env[key];
        } else {
          process.env[key] = val;
        }
      }
    }

    // Clean up test env vars and reset client
    delete process.env.S3_KEY_ID;
    delete process.env.S3_KEY_SECRET;
    delete process.env.S3_BUCKET;
    recording.resetRecordingSessionsForTest();

  } catch (err) {
    console.error("Test execution error:", err);
    failures++;
  } finally {
    server.close((closeErr) => {
      if (closeErr) console.error("Error closing test server:", closeErr);
      if (failures > 0) {
        process.exit(1);
      } else {
        console.log("All backend integration and CORS tests passed!");
        process.exit(0);
      }
    });
  }
});
