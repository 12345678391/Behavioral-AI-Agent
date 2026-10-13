const { AccessToken } = require("livekit-server-sdk");

async function createToken(identity = "candidate", requestedRoom) {
  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;

  if (!apiKey || !apiSecret) {
    throw new Error(
      "LiveKit credentials missing. Set LIVEKIT_API_KEY and LIVEKIT_API_SECRET."
    );
  }

  // Generate a unique room per interview session if none is provided
  const room =
    requestedRoom ||
    `behavior-room-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;

  const token = new AccessToken(apiKey, apiSecret, {
    identity: identity,
  });

  token.addGrant({
    roomJoin: true,
    room: room,
    canPublish: true,
    canSubscribe: true,
  });

  const jwt = await token.toJwt();

  return {
    token: jwt,
    room: room,
  };
}

module.exports = createToken;