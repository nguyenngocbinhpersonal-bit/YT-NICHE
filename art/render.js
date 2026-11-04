const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 2 });
  await p.goto('file://' + process.argv[2]);
  await p.screenshot({ path: process.argv[3] });
  await b.close();
})();
