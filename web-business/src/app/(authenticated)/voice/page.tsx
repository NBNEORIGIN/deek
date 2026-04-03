'use client'

import { useRef, useState, useCallback } from 'react'

type RecordingState = 'idle' | 'recording' | 'processing'

export default function VoicePage() {
  const [recordingState, setRecordingState] = useState<RecordingState>('idle')
  const [transcript, setTranscript] = useState<string>('')
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [error, setError] = useState<string>('')

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const startRecording = useCallback(async () => {
    setError('')
    setTranscript('')
    setSaveStatus('idle')

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setError('Microphone access denied. Please allow microphone access and try again.')
      return
    }

    chunksRef.current = []
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : 'audio/webm'

    const recorder = new MediaRecorder(stream, { mimeType })
    mediaRecorderRef.current = recorder

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data)
    }

    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop())
      setRecordingState('processing')

      const blob = new Blob(chunksRef.current, { type: mimeType })
      const formData = new FormData()
      formData.append('audio', blob, 'recording.webm')

      try {
        const res = await fetch('/api/voice/transcribe', {
          method: 'POST',
          body: formData,
        })
        if (!res.ok) {
          const data = await res.json().catch(() => ({}))
          setError(data.error ?? 'Transcription failed. Please try again.')
        } else {
          const data = await res.json()
          setTranscript(data.text ?? '')
        }
      } catch {
        setError('Network error during transcription.')
      } finally {
        setRecordingState('idle')
      }
    }

    recorder.start()
    setRecordingState('recording')
  }, [])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && recordingState === 'recording') {
      mediaRecorderRef.current.stop()
    }
  }, [recordingState])

  const handleToggle = () => {
    if (recordingState === 'idle') {
      startRecording()
    } else if (recordingState === 'recording') {
      stopRecording()
    }
  }

  const saveToMemory = async () => {
    if (!transcript) return
    setSaveStatus('saving')
    try {
      const res = await fetch('/api/voice/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: transcript }),
      })
      if (res.ok) {
        setSaveStatus('saved')
      } else {
        setSaveStatus('error')
      }
    } catch {
      setSaveStatus('error')
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6 md:space-y-8">
      {/* Record button */}
      <div className="flex flex-col items-center gap-6 py-10 md:py-8">
        <button
          onClick={handleToggle}
          disabled={recordingState === 'processing'}
          aria-label={recordingState === 'recording' ? 'Stop recording' : 'Start recording'}
          className={
            'relative w-28 h-28 md:w-24 md:h-24 rounded-full flex items-center justify-center transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ' +
            (recordingState === 'recording'
              ? 'bg-red-500 shadow-lg shadow-red-200 animate-pulse'
              : recordingState === 'processing'
              ? 'bg-slate-300 cursor-not-allowed'
              : 'bg-red-500 hover:bg-red-600 shadow-md')
          }
        >
          {recordingState === 'recording' ? (
            /* Stop icon */
            <span className="w-8 h-8 bg-white rounded-sm" />
          ) : recordingState === 'processing' ? (
            <svg className="w-8 h-8 text-white animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
          ) : (
            /* Mic icon */
            <svg className="w-10 h-10 text-white" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 1a4 4 0 00-4 4v6a4 4 0 008 0V5a4 4 0 00-4-4zm-1 18.93V21h-2v2h6v-2h-2v-1.07A7.003 7.003 0 0019 13h-2a5 5 0 01-10 0H5a7.003 7.003 0 006 6.93z" />
            </svg>
          )}
        </button>

        <p className="text-sm text-slate-500">
          {recordingState === 'recording'
            ? 'Recording… tap to stop'
            : recordingState === 'processing'
            ? 'Processing…'
            : 'Tap to start recording'}
        </p>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Transcript */}
      {transcript && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
          <h2 className="text-sm font-semibold text-slate-700">Transcript</h2>
          <p className="text-sm text-slate-800 leading-relaxed whitespace-pre-wrap">{transcript}</p>

          <div className="flex items-center gap-3">
            <button
              onClick={saveToMemory}
              disabled={saveStatus === 'saving' || saveStatus === 'saved'}
              className="px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed min-h-[44px]"
            >
              {saveStatus === 'saving'
                ? 'Saving…'
                : saveStatus === 'saved'
                ? 'Saved'
                : 'Save to Memory'}
            </button>

            {saveStatus === 'saved' && (
              <span className="text-sm text-green-600 font-medium">Saved successfully</span>
            )}
            {saveStatus === 'error' && (
              <span className="text-sm text-red-600">Save failed — try again</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
