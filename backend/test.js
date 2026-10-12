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
