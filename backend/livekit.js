const { AccessToken } = require("livekit-server-sdk");

const apiKey = process.env.LIVEKIT_API_KEY;
const apiSecret = process.env.LIVEKIT_API_SECRET;

async function createToken(identity) {
  const token = new AccessToken(apiKey, apiSecret, {
    identity: identity,
  });

  token.addGrant({
    roomJoin: true,
    room: "behavior-room",
    canPublish: true,
    canSubscribe: true,
  });

  return await token.toJwt();
}

module.exports = createToken;