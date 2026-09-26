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
  onDisconnected
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

      document.body.appendChild(audioElement);

      try {
        await audioElement.play();

        console.log(
          "AI audio playback started."
        );
      } catch (error) {
        console.error(
          "AI audio playback failed:",
          error
        );

        try {
          await room.startAudio();
          await audioElement.play();

          console.log(
            "AI audio started after startAudio()."
          );
        } catch (audioError) {
          console.error(
            "Could not start AI audio:",
            audioError
          );
        }
      }
    }
  );

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
        "Connected to LiveKit room:",
        room.name
      );

      try {
        await room.localParticipant.setMicrophoneEnabled(
          true
        );

        console.log(
          "Microphone enabled."
        );
      } catch (error) {
        console.error(
          "Microphone error:",
          error
        );
      }

      try {
        await room.startAudio();

        console.log(
          "LiveKit audio started."
        );
      } catch (error) {
        console.warn(
          "startAudio warning:",
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
        "Disconnected from LiveKit:",
        reason
      );

      if (onDisconnected) {
        onDisconnected();
      }
    }
  );

  // ==========================================================
  // CONNECT
  // ==========================================================

  console.log(
    "Connecting to LiveKit..."
  );

  await room.connect(
    serverUrl,
    token,
    {
      autoSubscribe: true,
    }
  );

  console.log(
    "Successfully connected to LiveKit."
  );

  return room;
}