# Enlivonex promo renders

`Enlivonex-Early-Access-Explainer-Animated.mp4` is the enhanced vertical cut.
It adds a gentle breathing zoom to the real app footage, animated step markers,
and sliding workflow labels. The `-Animated-Clicks` variant also adds punch-in
zooms and ripple highlights on the story topic, duration, output format, and
generate/progress controls, timed with original synthesized whoosh and click
effects. The original explainer remains unchanged.

To re-render with the project virtual environment:

```powershell
python -m pip install "moviepy>=2,<3"
.\.venv\Scripts\python.exe .\marketing\promos\make_animated_promo.py
.\.venv\Scripts\python.exe .\marketing\promos\make_animated_promo.py --output .\marketing\promos\Enlivonex-Early-Access-Explainer-Animated-Clicks.mp4
```

The current FFmpeg/NVIDIA driver combination on the build PC cannot use NVENC:
FFmpeg requires driver 610 or newer, while the installed driver is 592.82. The
delivered render therefore uses MoviePy compositing and CPU H.264 encoding.
After installing a compatible NVIDIA driver, GPU encoding can be requested with
`--gpu-encode`.
