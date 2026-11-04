// Render the GSAP/SVG scene: seek the paused timeline to each frame, screenshot, pipe to ffmpeg.
// Usage: node render.js [out.mp4] [--stills t1,t2,...]
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

(async () => {
  const args = process.argv.slice(2);
  const si = args.indexOf('--stills');
  const stills = si >= 0 ? args[si + 1].split(',').map(Number) : null;
  const out = path.resolve(args.find(a => a.endsWith('.mp4')) || path.join(__dirname, 'out', 'stickman_gsap_5s.mp4'));

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('file://' + path.join(__dirname, 'scene.html'));
  await page.waitForFunction(() => window.READY === true);
  const grab = async t => { await page.evaluate(t => window.renderFrame(t), t); return page.screenshot({ type: 'png' }); };

  if (stills) {
    const dir = path.join(__dirname, 'out', 'stills'); fs.mkdirSync(dir, { recursive: true });
    for (const t of stills) fs.writeFileSync(path.join(dir, `t${t.toFixed(2)}.png`), await grab(t));
    await browser.close(); return;
  }
  const { DURATION, FPS } = await page.evaluate(() => ({ DURATION: window.DURATION, FPS: window.FPS }));
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const ff = spawn('ffmpeg', ['-y', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-movflags', '+faststart', out], { stdio: ['pipe', 'ignore', 'inherit'] });
  const total = Math.round(DURATION * FPS);
  for (let f = 0; f < total; f++) {
    const buf = await grab(f / FPS);
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
  }
  ff.stdin.end(); await new Promise(r => ff.on('close', r));
  await browser.close();
  console.log('wrote', out, total, 'frames');
})();
