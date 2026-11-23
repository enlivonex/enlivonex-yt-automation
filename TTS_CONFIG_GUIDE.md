# TTS_PROVIDER Configuration Guide

## Current Configuration
TTS_PROVIDER=mock  # Change this to enable real audio

## Option 1: FREE Offline TTS (Recommended for ₹0 budget)
TTS_PROVIDER=offline

Requirements:
- Install pyttsx3: pip install pyttsx3
- Uses Windows built-in SAPI voices (already on your system)
- Zero cost, works offline, no API keys needed

Run: install_offline_tts.bat

## Option 2: Paid OpenAI TTS (Requires API key)
TTS_PROVIDER=openai
OPENAI_API_KEY=sk-proj-your-key-here

Cost: ~$0.015 per 1000 characters (~$0.001 per 15-second narration)

## Option 3: Paid ElevenLabs TTS (Requires API key + voice ID)
TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=your-key-here
ELEVENLABS_VOICE_ID=your-voice-id-here

Cost: Variable based on plan
