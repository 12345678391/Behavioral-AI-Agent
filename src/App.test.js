import { render, screen, fireEvent, act } from '@testing-library/react';
import App from './App';
import * as api from './api';
import * as livekitRoom from './LiveKitRoom';

jest.mock('./api', () => ({
  getToken: jest.fn(),
  getAssessmentResult: jest.fn(),
}));

jest.mock('./LiveKitRoom', () => ({
  joinRoom: jest.fn(),
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
});
