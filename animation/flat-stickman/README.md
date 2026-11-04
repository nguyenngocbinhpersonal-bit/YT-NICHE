# Flat stickman scene (5s, code-only)

A 5-second 1920x1080 / 30fps scene in the flat-vector "stickman" style, drawn entirely with HTML Canvas primitives. No image assets and no animation software.

- `scene.html` has the whole scene. Open it in a browser for a live preview loop.
  - World: one-point perspective `P(X,Y,Z)`, procedural buildings, water tanks, poles, road.
  - Characters: one parametric template with a cut-out rig: shoulder and elbow joints, swap hands (open/point/rag), blink, eyelids, brows, mouth flaps, 2.5D head turn, breathing.
  - Timeline: keyframes with `kf()` and easing. Overlays: speech bubble and a lie counter with a pop. Post: vignette, grain, fades.
- `render.js` uses headless Chromium (Playwright) to render each frame, then pipes the PNGs into ffmpeg to make the MP4.

```bash
NODE_PATH=$(npm root -g) node render.js                 # -> out/flat_stickman_5s.mp4
NODE_PATH=$(npm root -g) node render.js --stills 1,3.5  # -> out/stills/*.png
```
