# Windows Task Scheduler Setup for Daily YouTube Shorts

## Overview
Generate 2 YouTube Shorts automatically every day at a scheduled time using Windows Task Scheduler.

## Prerequisites
1. Install pyttsx3: Run `install_offline_tts.bat`
2. Update `.env`:
   ```
   TTS_PROVIDER=offline
   STORY_PROVIDER=gemini  # or "template" for fully offline
   ```
3. Test the batch script manually first:
   ```
   cd "C:\automation test"
   python daily_batch.py
   ```

## Create Scheduled Task

### Option A: Using XML Import (Fastest)
1. Save the XML file below as `youtube_shorts_task.xml`
2. Open Task Scheduler (press Win+R, type `taskschd.msc`)
3. Click "Import Task" in the right panel
4. Browse to `youtube_shorts_task.xml`
5. Review settings and click OK

### Option B: Manual Setup
1. Open Task Scheduler (Win+R, type `taskschd.msc`)
2. Click "Create Task" (not "Create Basic Task")
3. **General tab:**
   - Name: `YouTube Shorts Daily Generation`
   - Description: `Generate 2 YouTube Shorts per day with offline TTS`
   - Run whether user is logged on or not: ☑
   - Run with highest privileges: ☑
4. **Triggers tab:**
   - Click "New"
   - Begin the task: On a schedule
   - Daily, at: `09:00:00` (adjust to your preference)
   - Enabled: ☑
5. **Actions tab:**
   - Click "New"
   - Action: Start a program
   - Program/script: `C:\automation test\.venv\Scripts\python.exe`
   - Arguments: `daily_batch.py`
   - Start in: `C:\automation test`
6. **Conditions tab:**
   - Uncheck "Start only if the computer is on AC power"
   - Check "Wake the computer to run this task" (optional)
7. **Settings tab:**
   - Allow task to be run on demand: ☑
   - Stop task if it runs longer than: `30 minutes`
   - If the task is already running: `Do not start a new instance`

## Task Scheduler XML Template

Save this as `youtube_shorts_task.xml` and import it:

```xml
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Generate 2 YouTube Shorts per day with offline TTS at ₹0 cost</Description>
    <URI>\YouTube Shorts Daily Generation</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-10-12T09:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <DisallowStartOnRemoteAppSession>false</DisallowStartOnRemoteAppSession>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>C:\automation test\.venv\Scripts\python.exe</Command>
      <Arguments>daily_batch.py</Arguments>
      <WorkingDirectory>C:\automation test</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
```

## Testing the Task

### Run Manually
1. Open Task Scheduler
2. Find "YouTube Shorts Daily Generation" in the task list
3. Right-click → Run
4. Check logs in `C:\automation test\logs\daily_batch_YYYY-MM-DD.log`

### Test Command Line
```powershell
cd "C:\automation test"
python daily_batch.py
```

Expected output:
- Creates 2 MP4 files in `data/jobs/JOB-YYYYMMDD-XXXXXX/output.mp4`
- Logs to `logs/daily_batch_YYYY-MM-DD.log`
- Second run same day: "Daily batch already completed"

## Pause/Resume Schedule

### Pause (Stop Generating)
1. Open Task Scheduler
2. Find "YouTube Shorts Daily Generation"
3. Right-click → Disable

Or use PowerShell:
```powershell
Disable-ScheduledTask -TaskName "YouTube Shorts Daily Generation"
```

### Resume (Start Generating Again)
1. Open Task Scheduler
2. Find "YouTube Shorts Daily Generation"
3. Right-click → Enable

Or use PowerShell:
```powershell
Enable-ScheduledTask -TaskName "YouTube Shorts Daily Generation"
```

### Delete Task
1. Open Task Scheduler
2. Find "YouTube Shorts Daily Generation"
3. Right-click → Delete

Or use PowerShell:
```powershell
Unregister-ScheduledTask -TaskName "YouTube Shorts Daily Generation" -Confirm:$false
```

## Monitoring

### Check Last Run
1. Open Task Scheduler
2. Select "YouTube Shorts Daily Generation"
3. Check "Last Run Time" and "Last Run Result" in the bottom panel

### View Logs
```powershell
cd "C:\automation test\logs"
notepad daily_batch_2026-10-11.log  # Replace with current date
```

### Check Generated Videos
```powershell
cd "C:\automation test\data\jobs"
dir /s output.mp4  # List all generated videos
```

## Troubleshooting

### Task Doesn't Run
- Check Task Scheduler history (View → Enable History)
- Verify Python path: `C:\automation test\.venv\Scripts\python.exe`
- Ensure working directory is set: `C:\automation test`
- Check user permissions (run with highest privileges)

### No Audio in Videos
- Verify TTS_PROVIDER=offline in .env
- Test pyttsx3: `python -c "import pyttsx3; pyttsx3.init().say('test'); pyttsx3.init().runAndWait()"`
- Check logs for TTS errors

### Duplicate Videos
The script automatically prevents duplicates by checking the database.
If you want to force re-generation, manually update the database or delete today's completed jobs.

### Failed Generation
- Check log file for specific errors
- Verify source clips exist: `python check_clips.py`
- Test manually: `python daily_batch.py`

## Cost Analysis

**Total ongoing cost: ₹0/month**

- Offline TTS (pyttsx3): Free, uses Windows SAPI
- Story generation:
  - Template mode: Free, offline
  - Gemini mode: Free tier (60 requests/minute)
- FFmpeg: Free, open source
- Source clips: Assume you have rights/license
- Storage: Local disk only

**Resource usage per day:**
- API calls: 2 (Gemini story generation, if enabled)
- Disk space: ~30-40 MB (2 videos × 15-20 MB each)
- CPU time: ~2-5 minutes total processing
- RAM: ~500 MB peak during rendering

## Next Steps After Scheduling

1. **Collect Videos**: Check `data/jobs/` daily
2. **Review Quality**: Listen to narration, check captions
3. **Upload Manually**: Use YouTube Studio to upload
4. **Optimize**:
   - Adjust scheduled time to match your workflow
   - Fine-tune voice speed in `app/voiceover/offline_provider.py`
   - Modify story templates if needed
   - Add more source clips for variety
