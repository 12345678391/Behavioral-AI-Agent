import {
  Room,
  RoomEvent,
  Track,
} from "livekit-client";

export async function joinRoom(
  token,
  serverUrl,
  onData,
  onConnected,
  onDisconnected,
  onMicrophoneError
) {
  const room = new Room({
    adaptiveStream: true,
    dynacast: true,
  });

  // ==========================================================
  // AI AUDIO
  // ==========================================================

  room.on(
    RoomEvent.TrackSubscribed,
    async (track, publication, participant) => {
      console.log(
        "Track subscribed:",
        track.kind,
        "from:",
        participant.identity
      );

      if (track.kind !== Track.Kind.Audio) {
        return;
      }

      console.log("AI audio track received.");

      const audioElement = track.attach();
      audioElement.autoplay = true;
      audioElement.muted = false;
      audioElement.volume = 1;
      audioElement.style.display = "none";
      audioElement.setAttribute("data-livekit-track-sid", track.sid);

      document.body.appendChild(audioElement);

      try {
        await audioElement.play();
        console.log("AI audio playback started.");
      } catch (error) {
        console.error("AI audio playback failed:", error);
        try {
          await room.startAudio();
          await audioElement.play();
          console.log("AI audio started after startAudio().");
        } catch (audioError) {
          console.error("Could not start AI audio:", audioError);
        }
      }
    }
  );

  // ==========================================================
  // TRACK UNSUBSCRIBED (CLEANUP AUDIO ELEMENTS)
  // ==========================================================

  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      console.log("Detaching audio track:", track.sid);
      const detached = track.detach();
      detached.forEach((element) => {
        if (element && element.parentNode) {
          element.parentNode.removeChild(element);
        }
      });
    }
  });

  // ==========================================================
  // DATA FROM PYTHON AGENT
  // ==========================================================

  room.on(
    RoomEvent.DataReceived,
    (
      payload,
      participant,
      kind,
      topic
    ) => {
      try {
        const text =
          new TextDecoder().decode(payload);

        const data = JSON.parse(text);

        console.log(
          "LiveKit assessment event:",
          data
        );

        if (onData) {
          onData(data);
        }
      } catch (error) {
        console.error(
          "Could not read LiveKit data:",
          error
        );
      }
    }
  );

  // ==========================================================
  // CONNECTED
  // ==========================================================

  room.on(
    RoomEvent.Connected,
    async () => {
      console.log(
        "[LiveKit] Connected to room:",
        room.name
      );

      console.log("[LiveKit] Microphone publication started.");

      try {
        await room.localParticipant.setMicrophoneEnabled(
          true
        );

        console.log(
          "[LiveKit] Microphone publication completed successfully."
        );
      } catch (error) {
        const isCancelled =
          error?.message?.includes("Cancelled publication") ||
          room.state === "disconnected";

        if (isCancelled) {
          console.log(
            "[LiveKit] Microphone publication was cancelled (room disconnected or track unpublished)."
          );
        } else {
          console.error(
            "[LiveKit] Microphone setup error:",
            error?.message || error
          );

          if (onMicrophoneError) {
            onMicrophoneError(error);
          }
        }
      }

      try {
        await room.startAudio();

        console.log(
          "[LiveKit] LiveKit audio playback started."
        );
      } catch (error) {
        console.warn(
          "[LiveKit] startAudio warning:",
          error
        );
      }

      if (onConnected) {
        onConnected();
      }
    }
  );

  // ==========================================================
  // DISCONNECTED
  // ==========================================================

  room.on(
    RoomEvent.Disconnected,
    (reason) => {
      console.log(
        "[LiveKit] Room disconnected event received. Reason:",
        reason
      );

      // Clean up any remaining attached audio elements
      const elements = document.querySelectorAll(
        "[data-livekit-track-sid]"
      );
      elements.forEach((el) => {
        if (el.parentNode) {
          el.parentNode.removeChild(el);
        }
      });

      if (onDisconnected) {
        onDisconnected(reason);
      }
    }
  );

  // ==========================================================
  // CONNECT
  // ==========================================================

  console.log(
    "[LiveKit] Room connection started..."
  );

  try {
    await room.connect(
      serverUrl,
      token,
      {
        autoSubscribe: true,
      }
    );

    console.log(
      "[LiveKit] Room connection completed. Room name:",
      room.name
    );
  } catch (error) {
    console.error(
      "[LiveKit] Failed to connect to room:",
      error?.message || error
    );
    throw error;
  }

  return room;
}