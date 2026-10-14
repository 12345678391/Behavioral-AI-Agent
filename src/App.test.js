import { render, screen, fireEvent, act } from '@testing-library/react';
import App from './App';
import * as api from './api';
import * as livekitRoom from './LiveKitRoom';

jest.mock('./api', () => ({
  getToken: jest.fn(),
  getAssessmentResult: jest.fn(),
  startRecording: jest.fn().mockResolvedValue({ success: true, status: 'active' }),
  stopRecording: jest.fn().mockResolvedValue({ success: true, status: 'finalizing' }),
  getRecordingStatus: jest.fn().mockResolvedValue({
    success: true,
    status: 'completed',
    videoUrl: 'https://storage.example.com/interview.mp4',
  }),
}));

jest.mock('./LiveKitRoom', () => ({
  joinRoom: jest.fn(),
  pauseRoomAudio: jest.fn().mockResolvedValue(),
  resumeRoomAudio: jest.fn().mockResolvedValue(),
}));

describe('Behavioral AI Agent App', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders Behavioral AI assessment landing page', () => {
    render(<App />);

    const headings = screen.getAllByText(/Behavioral AI/i);
    expect(headings.length).toBeGreaterThan(0);

    const startButton = screen.getByRole('button', { name: /Start Assessment/i });
    expect(startButton).toBeInTheDocument();
    expect(startButton).not.toBeDisabled();

    expect(screen.getByText(/Ready/i)).toBeInTheDocument();
  });

  test('normal startup: clicking start connects room and transitions state', async () => {
    const mockRoom = {
      name: 'test-room',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn(),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    expect(api.getToken).toHaveBeenCalledTimes(1);
    expect(livekitRoom.joinRoom).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Assessment Active/i)).toBeInTheDocument();
  });

  test('repeated start clicks: guard prevents duplicate connection attempts', async () => {
    let resolveToken;
    const tokenPromise = new Promise((resolve) => {
      resolveToken = resolve;
    });
    api.getToken.mockReturnValue(tokenPromise);

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    // Click multiple times rapidly
    act(() => {
      fireEvent.click(startButton);
      fireEvent.click(startButton);
      fireEvent.click(startButton);
    });

    // Guard should ensure getToken is called exactly once
    expect(api.getToken).toHaveBeenCalledTimes(1);
    expect(startButton).toBeDisabled();
    expect(screen.getByText(/Connecting to AI Interviewer.../i)).toBeInTheDocument();

    // Resolve token to finish cleanly
    await act(async () => {
      resolveToken({ token: 'fake-jwt', serverUrl: 'wss://test.livekit.cloud', room: 'test-room' });
    });
  });

  test('connection failure: resets guard and displays error message', async () => {
    api.getToken.mockRejectedValueOnce(new Error('Network error reaching backend'));

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    expect(
      screen.getByText(/Could not start the assessment: Network error reaching backend/i)
    ).toBeInTheDocument();

    // Guard should be reset, button re-enabled
    expect(startButton).not.toBeDisabled();
    expect(screen.getByText(/Start Assessment/i)).toBeInTheDocument();
  });

  test('component unmount: disconnects active room cleanly', async () => {
    const mockRoom = {
      name: 'test-room-unmount',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn() },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-unmount',
    });
    livekitRoom.joinRoom.mockResolvedValueOnce(mockRoom);

    const { unmount } = render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    expect(mockRoom.disconnect).not.toHaveBeenCalled();

    // Unmount component
    unmount();

    expect(mockRoom.disconnect).toHaveBeenCalledTimes(1);
  });

  test('ending assessment: publishes end_assessment data without calling disconnect', async () => {
    const mockRoom = {
      name: 'test-room-end',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn().mockResolvedValue(true),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-end',
    });
    livekitRoom.joinRoom.mockResolvedValueOnce(mockRoom);

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    const endButton = screen.getByRole('button', { name: /End Assessment/i });
    await act(async () => {
      fireEvent.click(endButton);
    });

    expect(mockRoom.localParticipant.publishData).toHaveBeenCalledTimes(1);
    const [payload, opts] = mockRoom.localParticipant.publishData.mock.calls[0];
    const decoded = JSON.parse(new TextDecoder().decode(payload));
    expect(decoded.type).toBe('end_assessment');
    expect(opts.topic).toBe('behavioral-ui');

    // Must NOT disconnect immediately because python agent needs connection to speak closing message
    expect(mockRoom.disconnect).not.toHaveBeenCalled();
  });

  test('successful completion: receiving final_result displays dashboard and disconnects cleanly', async () => {
    let capturedOnData;
    const mockRoom = {
      name: 'test-room-success',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn() },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-success',
    });
    livekitRoom.joinRoom.mockImplementation((token, url, onData) => {
      capturedOnData = onData;
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });
    await act(async () => {
      fireEvent.click(startButton);
    });

    // Simulate receiving final_result
    const finalReportPayload = {
      type: 'final_result',
      data: {
        completed: true,
        final_analysis: {
          overall_score: 88,
          scores: {
            communication: 90,
            teamwork: 85,
            adaptability: 88,
            accountability: 82,
            decision_making: 92,
            leadership: 84,
            problem_solving: 87,
          },
          strengths: ['Clear articulate communication', 'Strong problem solving'],
          areas_for_improvement: ['Delegate more tasks to team members'],
          behavioral_summary: 'Candidate demonstrated exemplary structured leadership skills.',
          team_working_style: {
            collaboration: 80,
            independence: 85,
            summary: 'Highly collaborative and autonomous.',
          },
        },
      },
    };

    await act(async () => {
      capturedOnData(finalReportPayload);
    });

    // Verify assessment result dashboard is displayed
    expect(screen.getByText(/Behavioral Assessment Report/i)).toBeInTheDocument();
    expect(screen.getAllByText(/88/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Clear articulate communication/i)).toBeInTheDocument();
    expect(screen.getByText(/Candidate demonstrated exemplary structured leadership skills/i)).toBeInTheDocument();

    // Verify clean disconnect
    expect(mockRoom.disconnect).toHaveBeenCalledTimes(1);
  });

  test('failing completion: receiving assessment_error logs structured details and preserves recoverable conversation', async () => {
    let capturedOnData;
    let capturedOnDisconnected;
    const mockRoom = {
      name: 'test-room-error',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn() },
    };

    const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-error',
    });
    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected, onDisconnected) => {
      capturedOnData = onData;
      capturedOnDisconnected = onDisconnected;
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });
    await act(async () => {
      fireEvent.click(startButton);
    });

    // Send question turn to populate conversation
    await act(async () => {
      capturedOnData({
        type: 'interview_question',
        data: { question: 'Tell me about a difficult problem you resolved.' },
      });
    });

    expect(screen.getAllByText(/Tell me about a difficult problem you resolved/i).length).toBeGreaterThan(0);

    // Simulate receiving structured assessment_error
    const errorPayload = {
      type: 'assessment_error',
      data: {
        stage: 'final_analysis',
        code: 'APIConnectionError',
        http_status: 503,
        message: 'The interview was completed, but the final behavioral analysis could not be generated. Your interview data has been saved.',
        error_details: 'Service temporarily unavailable.',
        recoverable_data: {
          transcript: [
            { role: 'ai', text: 'Tell me about a difficult problem you resolved.' },
            { role: 'candidate', text: 'I redesigned the data ingestion pipeline to handle 10x traffic.' },
          ],
        },
      },
    };

    await act(async () => {
      capturedOnData(errorPayload);
    });

    // Verify structured error was logged
    expect(consoleErrorSpy).toHaveBeenCalledWith(
      expect.stringContaining("[App] ASSESSMENT ERROR in stage 'final_analysis' [code: APIConnectionError, status: 503]"),
      expect.anything()
    );

    // Simulate room disconnect event firing after error
    await act(async () => {
      if (capturedOnDisconnected) {
        capturedOnDisconnected('assessment_error');
      }
    });

    // Verify error review view displays saved notice and preserves conversation
    expect(screen.getByText(/Assessment Data Saved/i)).toBeInTheDocument();
    expect(screen.getByText(/The interview was completed, but the final behavioral analysis could not be generated/i)).toBeInTheDocument();
    expect(screen.getByText(/I redesigned the data ingestion pipeline to handle 10x traffic/i)).toBeInTheDocument();

    // Verify Start New Assessment button is rendered instead of resetting silently
    expect(screen.getByRole('button', { name: /Start New Assessment/i })).toBeInTheDocument();

    consoleErrorSpy.mockRestore();
  });

  test('audio autoplay policy: displays enable audio prompt when blocked and unblocks on user click', async () => {
    let capturedOnAudioPlaybackChanged = null;
    const mockRoom = {
      name: 'test-room',
      startAudio: jest.fn().mockResolvedValue(true),
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn(),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room',
    });

    livekitRoom.joinRoom.mockImplementation((
      token,
      url,
      onData,
      onConnected,
      onDisconnected,
      onMicError,
      onAudioStatus
    ) => {
      capturedOnAudioPlaybackChanged = onAudioStatus;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    // Connected state: Initially audio active
    expect(screen.getByText(/🔊 Audio Active/i)).toBeInTheDocument();

    // Simulate browser autoplay blocking audio (canPlay = false)
    await act(async () => {
      if (capturedOnAudioPlaybackChanged) {
        capturedOnAudioPlaybackChanged(false);
      }
    });

    // Check that audio blocked indicator and prompt are visible
    expect(screen.getByText(/🔇 Audio Blocked/i)).toBeInTheDocument();
    expect(screen.getByText(/AI Interviewer audio is paused by your browser/i)).toBeInTheDocument();

    const enableAudioBtn = screen.getByRole('button', { name: /Enable AI Audio/i });
    expect(enableAudioBtn).toBeInTheDocument();

    // User clicks Enable AI Audio
    await act(async () => {
      fireEvent.click(enableAudioBtn);
    });

    // Verify room.startAudio was invoked
    expect(mockRoom.startAudio).toHaveBeenCalledTimes(1);

    // Audio status returns to active
    expect(screen.getByText(/🔊 Audio Active/i)).toBeInTheDocument();
  });

  test('intentional disconnection: clicking End Assessment does not display connection closed error', async () => {
    let capturedOnDisconnected = null;
    const mockRoom = {
      name: 'test-room-end',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn().mockResolvedValue(true),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-end',
    });
    api.getAssessmentResult.mockResolvedValueOnce(null);

    livekitRoom.joinRoom.mockImplementation((
      token,
      url,
      onData,
      onConnected,
      onDisconnected,
      onMicError,
      onAudioStatus
    ) => {
      capturedOnDisconnected = onDisconnected;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    expect(screen.getByText(/Assessment Active/i)).toBeInTheDocument();

    const endButton = screen.getByRole('button', { name: /End Assessment/i });
    expect(endButton).toBeInTheDocument();

    // Candidate clicks End Assessment
    await act(async () => {
      fireEvent.click(endButton);
    });

    // Room disconnects as part of completion
    await act(async () => {
      if (capturedOnDisconnected) {
        capturedOnDisconnected('client_initiated');
      }
    });

    // Verify "Connection to the assessment was closed" is NOT shown
    expect(screen.queryByText(/Connection to the assessment was closed/i)).not.toBeInTheDocument();
  });

  test('end assessment delivery: final_result renders complete behavioral assessment report in UI', async () => {
    let capturedOnData = null;
    const mockRoom = {
      name: 'test-room-report',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn().mockResolvedValue(true),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-report',
    });

    livekitRoom.joinRoom.mockImplementation((
      token,
      url,
      onData,
      onConnected,
      onDisconnected
    ) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });
    await act(async () => {
      fireEvent.click(startButton);
    });

    // Deliver final_result
    await act(async () => {
      capturedOnData({
        type: 'final_result',
        data: {
          completed: true,
          final_analysis: {
            overall_score: 92,
            scores: {
              communication: 95,
              teamwork: 90,
              adaptability: 88,
              accountability: 91,
              decision_making: 94,
              leadership: 89,
              problem_solving: 92,
            },
            strengths: ['Clear and articulate communicator under pressure.'],
            areas_for_improvement: ['Delegate earlier in project milestones.'],
            behavioral_summary: 'Demonstrated outstanding collaborative problem-solving capabilities.',
            team_working_style: {
              summary: 'Proactive and supportive collaborator.',
            },
          },
        },
      });
    });

    // The full report must be rendered
    expect(screen.getByText(/Behavioral Assessment Report/i)).toBeInTheDocument();
    expect(screen.getByText(/Assessment Complete/i)).toBeInTheDocument();
    expect(screen.getAllByText(/92/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Clear and articulate communicator under pressure/i)).toBeInTheDocument();
  });

  test('end assessment idempotency: repeated clicks do not send duplicate publishData calls', async () => {
    const mockRoom = {
      name: 'test-room-clicks',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn().mockResolvedValue(true),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-clicks',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });
    await act(async () => {
      fireEvent.click(startButton);
    });

    const endButton = screen.getByRole('button', { name: /End Assessment/i });

    // Click multiple times rapidly
    await act(async () => {
      fireEvent.click(endButton);
      fireEvent.click(endButton);
      fireEvent.click(endButton);
    });

    // publishData must only be called once
    expect(mockRoom.localParticipant.publishData).toHaveBeenCalledTimes(1);
  });

  test('incomplete final_result (final_analysis: null) is not rendered as a valid 0-score report', async () => {
    let capturedOnData = null;
    const mockRoom = {
      name: 'test-room-incomplete',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn().mockResolvedValue(true),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-incomplete',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    // Deliver incomplete final_result with final_analysis: null
    await act(async () => {
      capturedOnData({
        type: 'final_result',
        data: {
          completed: false,
          final_analysis: null,
        },
      });
    });

    // Must NOT render the score overview with 0/100 scores
    expect(screen.queryByText(/Behavioral Score Overview/i)).not.toBeInTheDocument();
    // Must render Completed with Notice and offer Retry Report Retrieval
    expect(screen.getByText(/Completed with Notice/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry Report Retrieval/i })).toBeInTheDocument();
  });

  test('temporary getAssessmentResult failures retry within bounded limits and render report on success', async () => {
    jest.useFakeTimers();
    try {
      let capturedOnDisconnected = null;
      const mockRoom = {
        name: 'test-room-retry',
        disconnect: jest.fn(),
        localParticipant: {
          publishData: jest.fn().mockResolvedValue(true),
        },
      };

      api.getToken.mockResolvedValueOnce({
        token: 'fake-jwt',
        serverUrl: 'wss://test.livekit.cloud',
        room: 'test-room-retry',
      });

      // Fail first 2 attempts, succeed on 3rd attempt
      api.getAssessmentResult
        .mockRejectedValueOnce(new Error('Assessment result not found (404)'))
        .mockRejectedValueOnce(new Error('Assessment result not found (404)'))
        .mockResolvedValueOnce({
          assessment_id: 'test-room-retry',
          completed: true,
          final_analysis: {
            overall_score: 91,
            scores: {
              communication: 92,
              teamwork: 90,
            },
            strengths: ['Resilient under pressure.'],
            areas_for_improvement: ['None noted.'],
            behavioral_summary: 'Strong overall behavioral performance.',
          },
        });

      livekitRoom.joinRoom.mockImplementation((
        token,
        url,
        onData,
        onConnected,
        onDisconnected
      ) => {
        capturedOnDisconnected = onDisconnected;
        if (onConnected) onConnected();
        return Promise.resolve(mockRoom);
      });

      render(<App />);
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
      });

      // Click End Assessment so intentionalEndRef.current = true
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /End Assessment/i }));
      });

      // Trigger disconnect before final_result arrives over data channel
      await act(async () => {
        capturedOnDisconnected('client_initiated');
      });

      // Attempt 1 failed; advance 2000ms for Attempt 2
      await act(async () => {
        jest.advanceTimersByTime(2000);
      });

      // Attempt 2 failed; advance 2000ms for Attempt 3 (which resolves with valid report)
      await act(async () => {
        jest.advanceTimersByTime(2000);
      });

      expect(api.getAssessmentResult).toHaveBeenCalledTimes(3);
      expect(screen.getByText(/Behavioral Score Overview/i)).toBeInTheDocument();
      expect(screen.getByText(/Resilient under pressure/i)).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  test('endAssessment resets endingRef when publishData fails so candidate can retry', async () => {
    const mockRoom = {
      name: 'test-room-publish-fail',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest
          .fn()
          .mockRejectedValueOnce(new Error('Data channel temporarily unavailable'))
          .mockResolvedValueOnce(true),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-publish-fail',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    // First click fails publishData
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /End Assessment/i }));
    });

    expect(screen.getByText(/Could not complete the assessment/i)).toBeInTheDocument();

    // Second click must be allowed because endingRef was reset on failure
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /End Assessment/i }));
    });

    expect(mockRoom.localParticipant.publishData).toHaveBeenCalledTimes(2);
  });

  test('interim candidate transcription updates one message progressively and finalizes without duplication', async () => {
    let capturedOnData;
    const mockRoom = {
      name: 'test-room-interim',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn() },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-interim',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    // Interviewer asks question
    await act(async () => {
      capturedOnData({
        type: 'interview_question',
        data: { question: 'Tell me about a challenging project.' },
      });
    });

    expect(screen.getAllByText(/Tell me about a challenging project/i).length).toBeGreaterThan(0);

    // 1. First interim chunk arrives
    await act(async () => {
      capturedOnData({
        type: 'candidate_interim_transcript',
        data: { answer: 'I led the migration', is_final: false },
      });
    });

    expect(screen.getByText('I led the migration')).toBeInTheDocument();
    expect(screen.getByText(/speaking\.\.\./i)).toBeInTheDocument();
    expect(screen.getAllByText(/Candidate/i).length).toBe(1);

    // 2. Second interim chunk arrives - updates the SAME bubble
    await act(async () => {
      capturedOnData({
        type: 'candidate_interim_transcript',
        data: { answer: 'I led the migration to a microservices architecture', is_final: false },
      });
    });

    expect(screen.queryByText('I led the migration')).not.toBeInTheDocument();
    expect(screen.getByText('I led the migration to a microservices architecture')).toBeInTheDocument();
    expect(screen.getByText(/speaking\.\.\./i)).toBeInTheDocument();
    expect(screen.getAllByText(/Candidate/i).length).toBe(1);

    // 3. Final candidate answer arrives - converts interim bubble to final without duplication
    await act(async () => {
      capturedOnData({
        type: 'candidate_answer',
        data: {
          answer: 'I led the migration to a microservices architecture and reduced latency by 40%.',
          is_final: true,
        },
      });
    });

    expect(screen.getByText('I led the migration to a microservices architecture and reduced latency by 40%.')).toBeInTheDocument();
    expect(screen.queryByText(/speaking\.\.\./i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/Candidate/i).length).toBe(1);

    // 4. Repeated final candidate answer - deduplicated, still only 1 candidate message
    await act(async () => {
      capturedOnData({
        type: 'candidate_answer',
        data: {
          answer: 'I led the migration to a microservices architecture and reduced latency by 40%.',
          is_final: true,
        },
      });
    });

    expect(screen.getAllByText(/Candidate/i).length).toBe(1);
  });

  test('candidate interim transcript ignores empty strings, and is finalized when end assessment disconnects', async () => {
    let capturedOnData;
    let capturedOnDisconnected;
    const mockRoom = {
      name: 'test-room-empty-interim',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn().mockResolvedValue(true) },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-empty-interim',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected, onDisconnected) => {
      capturedOnData = onData;
      capturedOnDisconnected = onDisconnected;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    // Send empty interim transcript: should be ignored
    await act(async () => {
      capturedOnData({
        type: 'candidate_interim_transcript',
        data: { answer: '   ', is_final: false },
      });
    });

    expect(screen.getByText(/The assessment conversation will appear here/i)).toBeInTheDocument();
    expect(screen.queryByText(/speaking\.\.\./i)).not.toBeInTheDocument();

    // Send valid interim transcript
    await act(async () => {
      capturedOnData({
        type: 'candidate_interim_transcript',
        data: { answer: 'I was in the middle of saying...', is_final: false },
      });
    });

    expect(screen.getByText('I was in the middle of saying...')).toBeInTheDocument();
    expect(screen.getByText(/speaking\.\.\./i)).toBeInTheDocument();

    // End assessment and disconnect: in-flight interim bubble must finalize cleanly and be preserved in conversation review
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /End Assessment/i }));
    });

    await act(async () => {
      capturedOnDisconnected('intentional_conclusion');
    });

    expect(screen.getByText('I was in the middle of saying...')).toBeInTheDocument();
    expect(screen.queryByText(/speaking\.\.\./i)).not.toBeInTheDocument();
  });

  test('recording lifecycle: starts recording on connect, stops on end, and displays completed playback video', async () => {
    let capturedOnData = null;
    const mockRoom = {
      name: 'room-recording-complete',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn().mockResolvedValue(true),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'mock-rec-token',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'room-recording-complete',
    });

    api.startRecording.mockResolvedValueOnce({
      success: true,
      status: 'active',
      egressId: 'egress-123',
    });

    api.stopRecording.mockResolvedValueOnce({
      success: true,
      status: 'finalizing',
    });

    api.getRecordingStatus.mockResolvedValueOnce({
      success: true,
      room: 'room-recording-complete',
      status: 'completed',
      videoUrl: 'https://storage.example.com/interviews/room-recording-complete.mp4',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);

    // Start assessment
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    expect(api.startRecording).toHaveBeenCalledWith('room-recording-complete');

    // Deliver completed final assessment result
    await act(async () => {
      capturedOnData({
        type: 'final_result',
        data: {
          assessment_id: 'room-recording-complete',
          completed: true,
          recording: {
            status: 'completed',
            videoUrl: 'https://storage.example.com/interviews/room-recording-complete.mp4',
          },
          final_analysis: {
            overall_score: 88,
            scores: { communication: 90, teamwork: 86 },
            strengths: ['Great communication skills.'],
            areas_for_improvement: ['Continue honing problem solving.'],
            behavioral_summary: 'Overall strong behavioral performance.',
          },
        },
      });
    });

    expect(screen.getByTestId('recording-playback-section')).toBeInTheDocument();
    expect(screen.getByTestId('recording-video-player')).toBeInTheDocument();
    expect(screen.getByTestId('recording-video-player')).toHaveAttribute(
      'src',
      'https://storage.example.com/interviews/room-recording-complete.mp4'
    );
    expect(screen.getByTestId('recording-download-link')).toHaveAttribute(
      'href',
      'https://storage.example.com/interviews/room-recording-complete.mp4'
    );
    expect(screen.getByText(/Recording Available/i)).toBeInTheDocument();
  });

  test('recording UI: displays finalizing state with spinner when video is encoding', async () => {
    let capturedOnData = null;
    const mockRoom = {
      name: 'room-recording-finalizing',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn().mockResolvedValue(true) },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'mock-rec-token',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'room-recording-finalizing',
    });

    api.startRecording.mockResolvedValueOnce({ success: true, status: 'active' });
    api.stopRecording.mockResolvedValueOnce({ success: true, status: 'finalizing' });
    api.getRecordingStatus.mockResolvedValueOnce({
      success: true,
      room: 'room-recording-finalizing',
      status: 'finalizing',
      videoUrl: null,
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    await act(async () => {
      capturedOnData({
        type: 'final_result',
        data: {
          assessment_id: 'room-recording-finalizing',
          completed: true,
          recording: {
            status: 'finalizing',
            videoUrl: null,
          },
          final_analysis: {
            overall_score: 85,
            scores: { communication: 85 },
            strengths: ['Solid answers'],
            areas_for_improvement: ['None'],
            behavioral_summary: 'Good performance.',
          },
        },
      });
    });

    expect(screen.getByTestId('recording-playback-section')).toBeInTheDocument();
    expect(screen.getByTestId('recording-finalizing-container')).toBeInTheDocument();
    expect(screen.getByText(/Finalizing Interview Recording\.\.\./i)).toBeInTheDocument();
    expect(screen.queryByTestId('recording-video-player')).not.toBeInTheDocument();
  });

  test('recording UI: displays clear unavailable state when storage is not configured without fake URL', async () => {
    let capturedOnData = null;
    const mockRoom = {
      name: 'room-recording-unavailable',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn().mockResolvedValue(true) },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'mock-rec-token',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'room-recording-unavailable',
    });

    api.startRecording.mockResolvedValueOnce({
      success: false,
      status: 'failed',
      error: 'Recording storage destination is not configured.',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    await act(async () => {
      capturedOnData({
        type: 'final_result',
        data: {
          assessment_id: 'room-recording-unavailable',
          completed: true,
          recording: {
            status: 'unavailable',
            videoUrl: null,
            error: 'No storage destination configured (S3/Azure/GCP).',
          },
          final_analysis: {
            overall_score: 92,
            scores: { communication: 92 },
            strengths: ['Excellent clarity'],
            areas_for_improvement: ['None'],
            behavioral_summary: 'Excellent performance.',
          },
        },
      });
    });

    expect(screen.getByTestId('recording-playback-section')).toBeInTheDocument();
    expect(screen.getByTestId('recording-unavailable-container')).toBeInTheDocument();
    expect(screen.getAllByText(/Recording Unavailable/i).length).toBeGreaterThan(0);
    expect(screen.getByText('No storage destination configured (S3/Azure/GCP).')).toBeInTheDocument();
    expect(screen.queryByTestId('recording-video-player')).not.toBeInTheDocument();
    expect(screen.queryByTestId('recording-download-link')).not.toBeInTheDocument();
  });

  test('pause/resume: clicking Pause freezes timer, shows Paused badge/banner, and dispatches pause_interview', async () => {
    jest.useFakeTimers();
    let capturedOnData = null;
    const mockPublishData = jest.fn().mockResolvedValue(true);
    const mockRoom = {
      name: 'room-pause-test',
      disconnect: jest.fn(),
      localParticipant: { publishData: mockPublishData },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'mock-pause-token',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'room-pause-test',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    const badge = screen.getByTestId('interview-status-badge');
    expect(badge).toHaveTextContent('Assessment Active (Running)');
    const pauseBtn = screen.getByTestId('pause-resume-button');
    expect(pauseBtn).toHaveTextContent(/Pause \/ Mute/i);

    act(() => {
      jest.advanceTimersByTime(5000);
    });

    await act(async () => {
      fireEvent.click(pauseBtn);
    });

    expect(badge).toHaveTextContent('Paused');
    expect(pauseBtn).toHaveTextContent(/Resume Interview/i);
    expect(screen.getByTestId('paused-banner')).toBeInTheDocument();
    expect(livekitRoom.pauseRoomAudio).toHaveBeenCalledWith(mockRoom);

    expect(mockPublishData).toHaveBeenCalledTimes(1);
    const sentPayload = JSON.parse(new TextDecoder().decode(mockPublishData.mock.calls[0][0]));
    expect(sentPayload).toEqual({ type: 'pause_interview', room: 'room-pause-test' });
    expect(mockPublishData.mock.calls[0][1]).toEqual({ reliable: true, topic: 'behavioral-ui' });

    const timeBeforeAdvance = screen.getByText(/\d+:\d+/).textContent;

    act(() => {
      jest.advanceTimersByTime(10000);
    });

    const timeAfterAdvance = screen.getByText(/\d+:\d+/).textContent;
    expect(timeAfterAdvance).toBe(timeBeforeAdvance);

    await act(async () => {
      fireEvent.click(pauseBtn);
    });

    expect(badge).toHaveTextContent('Assessment Active (Running)');
    expect(pauseBtn).toHaveTextContent(/Pause \/ Mute/i);
    expect(screen.queryByTestId('paused-banner')).not.toBeInTheDocument();
    expect(livekitRoom.resumeRoomAudio).toHaveBeenCalledWith(mockRoom);

    act(() => {
      jest.advanceTimersByTime(3000);
    });

    const timeAfterResume = screen.getByText(/\d+:\d+/).textContent;
    expect(timeAfterResume).not.toBe(timeBeforeAdvance);

    jest.useRealTimers();
  });

  test('pause/resume: preserves current question and transcript across pause and resume', async () => {
    let capturedOnData = null;
    const mockRoom = {
      name: 'room-preserve-test',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn().mockResolvedValue(true) },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'mock-token',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'room-preserve-test',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    await act(async () => {
      capturedOnData({
        type: 'interview_question',
        data: { question: 'Tell me about a challenging technical design decision.' },
      });
      capturedOnData({
        type: 'candidate_answer',
        data: { answer: 'I decided to use distributed event queues to decouple ingestion.' },
      });
    });

    expect(screen.getAllByText('Tell me about a challenging technical design decision.').length).toBe(2);
    expect(screen.getByText('I decided to use distributed event queues to decouple ingestion.')).toBeInTheDocument();

    const pauseBtn = screen.getByTestId('pause-resume-button');

    await act(async () => {
      fireEvent.click(pauseBtn);
    });

    expect(screen.getAllByText('Tell me about a challenging technical design decision.').length).toBe(2);
    expect(screen.getByText('I decided to use distributed event queues to decouple ingestion.')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(pauseBtn);
    });

    expect(screen.getAllByText('Tell me about a challenging technical design decision.').length).toBe(2);
    expect(screen.getByText('I decided to use distributed event queues to decouple ingestion.')).toBeInTheDocument();
  });

  test('pause/resume: agent-driven interview_paused and interview_resumed events synchronize UI and timer', async () => {
    let capturedOnData = null;
    const mockRoom = {
      name: 'room-agent-sync',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn().mockResolvedValue(true) },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'mock-token',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'room-agent-sync',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      capturedOnData = onData;
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Start Assessment/i }));
    });

    const badge = screen.getByTestId('interview-status-badge');
    expect(badge).toHaveTextContent('Assessment Active (Running)');

    await act(async () => {
      capturedOnData({
        type: 'interview_paused',
        data: { remaining_seconds: 185 },
      });
    });

    expect(badge).toHaveTextContent('Paused');
    expect(screen.getByTestId('paused-banner')).toBeInTheDocument();
    expect(screen.getByText('03:05')).toBeInTheDocument();

    await act(async () => {
      capturedOnData({
        type: 'interview_resumed',
        data: { remaining_seconds: 185 },
      });
    });

    expect(badge).toHaveTextContent('Assessment Active (Running)');
    expect(screen.queryByTestId('paused-banner')).not.toBeInTheDocument();
  });
});


