# Stickman scene: SVG rig + GSAP (5s)

A night scene of a queue. The hero says "Eu nunca menti." ("I never lied."), his lie counter jumps from 12 to 13, the queue turns to look at him one by one, and he starts to sweat.

- `scene.html` builds the SVG with JS: procedural perspective street, plus characters as nested `<g>` groups (shoulder → elbow → hand).
- Every animated value lives in plain state objects. A single paused GSAP timeline tweens them, and `applyAll()` writes the state into the SVG attributes.
- `render.js` calls `tl.seek(t)` for each frame, takes a Playwright screenshot, and pipes it into ffmpeg to make the MP4.

```bash
npm install                                  # gsap
NODE_PATH=$(npm root -g) node render.js      # -> out/stickman_gsap_5s.mp4
```
Opening `scene.html` in a browser plays the timeline in a live loop.
