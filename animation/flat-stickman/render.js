// Render scene.html frame-by-frame with headless Chromium and pipe PNGs into ffmpeg.
// Usage: node render.js [out.mp4] [--stills t1,t2,...]
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

(async () => {
  const args = process.argv.slice(2);
  const stillsIdx = args.indexOf('--stills');
  const stills = stillsIdx >= 0 ? args[stillsIdx + 1].split(',').map(Number) : null;
  const out = path.resolve(args.find(a => a.endsWith('.mp4')) || path.join(__dirname, 'out', 'flat_stickman_5s.mp4'));

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('file://' + path.join(__dirname, 'scene.html'));
  await page.waitForFunction(() => window.READY === true);
  const grab = async t => {
    const b64 = await page.evaluate(t => { window.renderFrame(t); return document.getElementById('c').toDataURL('image/png').split(',')[1]; }, t);
    return Buffer.from(b64, 'base64');
  };

  if (stills) {
    fs.mkdirSync(path.join(__dirname, 'out', 'stills'), { recursive: true });
    for (const t of stills) fs.writeFileSync(path.join(__dirname, 'out', 'stills', `t${t.toFixed(2)}.png`), await grab(t));
    await browser.close(); return;
  }

  const { DURATION, FPS } = await page.evaluate(() => ({ DURATION: window.DURATION, FPS: window.FPS }));
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const ff = spawn('ffmpeg', ['-y', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'medium', '-movflags', '+faststart', out],
    { stdio: ['pipe', 'ignore', 'inherit'] });
  const total = Math.round(DURATION * FPS);
  for (let f = 0; f < total; f++) {
    const buf = await grab(f / FPS);
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
  }
  ff.stdin.end();
  await new Promise(r => ff.on('close', r));
  await browser.close();
  console.log('wrote', out, total, 'frames');
})();
