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

    // Test 3: Non-existent assessmentId (expect 404)
    const res3 = await fetch(`http://localhost:${port}/api/assessment-result?assessmentId=test-nonexistent-123`);
    const data3 = await res3.json();
    if (res3.status === 404 && data3.error) {
      console.log("PASS: Nonexistent assessmentId returned 404");
    } else {
      console.error("FAIL: Nonexistent assessmentId test", res3.status, data3);
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

    // Test 8: Safe Vercel preview deployment origin allowed
    const previewOrigin = "https://behavioral-ai-agent-src-git-main-test.vercel.app";
    const res8 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: previewOrigin },
    });
    const allowOrigin8 = res8.headers.get("access-control-allow-origin");
    if (res8.status === 200 && allowOrigin8 === previewOrigin) {
      console.log("PASS: Vercel preview deployment origin allowed");
    } else {
      console.error("FAIL: Vercel preview deployment CORS test", res8.status, allowOrigin8);
      failures++;
    }

    // Test 9: Localhost development origin allowed
    const localOrigin = "http://localhost:3000";
    const res9 = await fetch(`http://localhost:${port}/health`, {
      headers: { Origin: localOrigin },
    });
    const allowOrigin9 = res9.headers.get("access-control-allow-origin");
    if (res9.status === 200 && allowOrigin9 === localOrigin) {
      console.log("PASS: Localhost dev origin allowed");
    } else {
      console.error("FAIL: Localhost CORS test", res9.status, allowOrigin9);
      failures++;
    }

    // Test 10: OPTIONS preflight response with methods and headers
    const res10 = await fetch(`http://localhost:${port}/token`, {
      method: "OPTIONS",
      headers: {
        Origin: prodOrigin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Content-Type",
      },
    });
    const allowOrigin10 = res10.headers.get("access-control-allow-origin");
    const allowMethods10 = res10.headers.get("access-control-allow-methods");
    if (res10.status === 204 && allowOrigin10 === prodOrigin && allowMethods10) {
      console.log("PASS: Preflight OPTIONS request handled properly (HTTP 204)");
    } else {
      console.error("FAIL: OPTIONS preflight test", res10.status, allowOrigin10, allowMethods10);
      failures++;
    }

    // Test 11: Unauthorized origin rejected by CORS policy
    const res11 = await fetch(`http://localhost:${port}/token?identity=candidate-test`, {
      headers: { Origin: "https://unauthorized-evil-site.com" },
    });
    if (res11.status === 403) {
      console.log("PASS: Unauthorized origin rejected with HTTP 403");
    } else {
      console.error("FAIL: Unauthorized origin rejection test", res11.status);
      failures++;
    }

    // Test 12: Lookalike attacker domain rejected by CORS policy
    const res12 = await fetch(`http://localhost:${port}/token?identity=candidate-test`, {
      headers: { Origin: "https://evil-behavioral-ai-agent-src.vercel.app" },
    });
    if (res12.status === 403) {
      console.log("PASS: Lookalike domain rejected with HTTP 403");
    } else {
      console.error("FAIL: Lookalike domain rejection test", res12.status);
      failures++;
    }
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
