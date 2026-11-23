@echo off
REM Install pyttsx3 for offline TTS
echo Installing pyttsx3 (offline TTS library)...
.venv\Scripts\python.exe -m pip install pyttsx3

echo.
echo Installation complete!
echo.
echo Testing pyttsx3...
.venv\Scripts\python.exe -c "import pyttsx3; engine = pyttsx3.init(); print('pyttsx3 version:', pyttsx3.__version__ if hasattr(pyttsx3, '__version__') else 'installed'); voices = engine.getProperty('voices'); print(f'Available voices: {len(voices)}'); engine.say('Testing offline text to speech'); engine.runAndWait(); print('Test complete!')"
