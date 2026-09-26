```jsx
import { useEffect, useState } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  ControlBar,
} from "@livekit/components-react";

import "@livekit/components-styles";

function App() {
  const [token, setToken] = useState("");
  const [serverUrl, setServerUrl] = useState("");

  useEffect(() => {
    fetch("http://localhost:3001/token")
      .then((res) => res.json())
      .then((data) => {
        setToken(data.token);
        setServerUrl(data.serverUrl);
      })
      .catch((error) => {
        console.error("Failed to get LiveKit token:", error);
      });
  }, []);

  return (
    <LiveKitRoom
      serverUrl={serverUrl}
      token={token}
      connect={Boolean(token && serverUrl)}
    >
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          background: "#f5f7fb",
          fontFamily: "Arial, sans-serif",
          gap: "20px",
        }}
      >
        <h1 style={{ margin: 0 }}>Behavioral AI</h1>

        <p style={{ margin: 0 }}>
          Your AI voice assistant
        </p>

        <ControlBar />

        <RoomAudioRenderer />
      </div>
    </LiveKitRoom>
  );
}

export default App;
```
