// Run with Playwright available through NODE_PATH: node tests/upload_selection.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const staticDir = path.join(__dirname, '..', 'static');
const fixture = name => ({rois:[{number:1,name}],suggested_targets:[1],warnings:[]});
const file = name => ({name, mimeType:'application/dicom', buffer:Buffer.from('ORIGINAL_PRIVATE_BYTES')});

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage();
    const errors = [], bodies = [], predictBodies = [];
    let active = 0, maxActive = 0, releaseFirst, predictStarted = false, releasePrediction;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://dose.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/')) {
        if (url.pathname === '/api/auth/session') return route.fulfill({json:{enabled:false,authenticated:false}});
        if (url.pathname === '/api/inspect') {
          bodies.push(route.request().postData()); active++; maxActive=Math.max(maxActive,active);
          const number = bodies.length;
          if (number === 1) await new Promise(resolve => {releaseFirst = resolve;});
          active--;
          return route.fulfill(number === 2 ? {status:429,json:{detail:'Server busy'}} : {json:fixture(number === 1 ? 'STALE_TARGET' : 'LATEST_TARGET')});
        }
        if (url.pathname === '/api/predict') {
          predictBodies.push(route.request().postData());
          predictStarted = true;
          await new Promise(resolve => {releasePrediction = resolve;});
          return route.fulfill({json:{summary:{prescription_Gy:20},metrics:[],dvhs:[],targets3d:[]}});
        }
        return route.fulfill({json:{}});
      }
      const name = url.pathname === '/' ? 'index.html' : path.basename(url.pathname);
      return route.fulfill({body:fs.readFileSync(path.join(staticDir,name)),contentType:name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'application/javascript'});
    });
    await page.goto('http://dose.test/');
    await page.waitForFunction(() => !document.getElementById('structure-file').disabled);
    await page.evaluate(() => {
      window.forgotten = 0;
      window.DosePrivacy = {
        prepare:async (structure,dose) => ({structure:new File(['SANITIZED_STRUCTURE'],'RTSTRUCT.dcm'),dose:dose ? new File([dose.name.includes('latest')?'SANITIZED_LATEST_DOSE':'SANITIZED_OLD_DOSE'],'RTDOSE.dcm') : null,context:{}}),
        forget:() => {window.forgotten++;}
      };
    });
    await page.locator('#structure-file').setInputFiles(file('private-structure.dcm'));
    await page.waitForTimeout(250); assert.equal(bodies.length,0,'selection alone must not upload');
    await page.locator('#process-button').click();
    await page.waitForFunction(() => document.getElementById('status').textContent.includes('Referenzdosis'));
    await assert.doesNotReject(async () => {while (!releaseFirst) await new Promise(r=>setTimeout(r,10));});
    assert.equal(await page.locator('#dose-file').isEnabled(),true);
    await page.locator('#dose-file').setInputFiles(file('private-old-dose.dcm'));
    await page.locator('#dose-file').setInputFiles(file('private-latest-dose.dcm'));
    await page.locator('#process-button').click();
    assert.equal(bodies.length,1,'do not overlap server inspections');
    assert.equal(await page.locator('#prescription').inputValue(),'');
    releaseFirst();
    await page.waitForFunction(() => document.getElementById('roi-list').textContent.includes('LATEST_TARGET'));
    assert.equal(bodies.length,3,'one latest pair and its bounded 429 retry');
    assert.equal(maxActive,1);
    assert(!await page.locator('#roi-list').textContent().then(text=>text.includes('STALE_TARGET')));
    assert(!bodies[0].includes('RTDOSE.dcm'));
    assert(bodies[1].includes('RTDOSE.dcm') && bodies[1].includes('SANITIZED_LATEST_DOSE'));
    assert(!bodies.some(body=>body.includes('private-') || body.includes('ORIGINAL_PRIVATE_BYTES')));
    await page.locator('#prescription').fill('20');
    assert.equal(await page.locator('#prediction-variant').inputValue(),'calibrated','calibrated default');
    await page.locator('#prediction-variant').selectOption('0');
    await page.locator('#predict-button').click();
    await page.waitForFunction(() => document.getElementById('status').textContent.includes('Geometrie'));
    assert.equal(await page.locator('#dose-file').isDisabled(),true,'prediction keeps file pickers locked');
    assert.equal(await page.locator('#prediction-variant').isDisabled(),true,'variant locked while computing');
    assert(predictBodies[0].includes('name="regularization_sigma_mm"\r\n\r\n0'),'raw variant submitted');
    assert.equal(predictStarted,true); releasePrediction();
    await page.waitForFunction(() => !document.getElementById('dose-file').disabled);

    const retained=await page.evaluate(()=>({prepared:preparedUpload.context,rx:$('prescription').value,files:[$('structure-file').files[0].name,$('dose-file').files[0].name]}));
    await page.locator('#prediction-variant').selectOption('1');
    assert.equal(await page.locator('#results').isHidden(),true,'changing variant invalidates result');
    assert.equal(await page.locator('#prescription').inputValue(),retained.rx);
    assert.deepEqual(await page.evaluate(()=>[$('structure-file').files[0].name,$('dose-file').files[0].name]),retained.files,'variant preserves uploads');
    assert.equal(await page.locator('#predict-button').isEnabled(),true,'ready to recompute without reinspection');

    // A slow asynchronous anonymizer must also leave pickers available and discard
    // the old context before preparing the latest pair.
    await page.evaluate(() => {
      window.prepareCalls=0;
      window.DosePrivacy.prepare=async (structure,dose) => {
        window.prepareCalls++;
        if(window.prepareCalls===1) await new Promise(resolve=>window.releasePrepare=resolve);
        return {structure:new File(['SANITIZED_STRUCTURE'],'RTSTRUCT.dcm'),dose:dose?new File(['SANITIZED_LATEST_DOSE'],'RTDOSE.dcm'):null,context:{}};
      };
    });
    const before=bodies.length;
    await page.locator('#structure-file').setInputFiles(file('private-replacement.dcm'));
    await page.locator('#process-button').click();
    await page.waitForFunction(()=>Boolean(window.releasePrepare));
    assert.equal(await page.locator('#dose-file').isEnabled(),true);
    await page.locator('#dose-file').setInputFiles(file('private-latest-dose-2.dcm'));
    await page.locator('#process-button').click();
    await page.evaluate(()=>window.releasePrepare());
    await page.waitForFunction(()=>window.prepareCalls===2 && document.getElementById('roi-list').textContent.includes('LATEST_TARGET'));
    assert.equal(bodies.length,before+1,'stale prepared pair is never uploaded');
    assert.equal(await page.locator('#prescription').inputValue(),'','old prescription is cleared');
    assert(await page.evaluate(()=>window.forgotten)>=2);
    const callsBeforeClear=bodies.length, forgottenBeforeClear=await page.evaluate(()=>window.forgotten);
    await page.locator('#clear-dose-file').click();
    assert.equal(await page.locator('#dose-file').evaluate(input=>input.files.length),0);
    assert.equal(await page.locator('#structure-file').evaluate(input=>input.files.length),1,'clear dose keeps structure');
    assert.equal(await page.locator('#predict-button').isDisabled(),true);
    assert(await page.evaluate(()=>window.forgotten)>forgottenBeforeClear,'clear discards original-UID mapping');
    await page.locator('#clear-structure-file').click();
    assert.equal(await page.locator('#structure-file').evaluate(input=>input.files.length),0);
    assert.equal(await page.locator('#process-button').isDisabled(),true);
    assert.equal(await page.locator('#clear-structure-file').isHidden(),true);
    await page.waitForTimeout(250);
    assert.equal(bodies.length,callsBeforeClear,'clear never starts an upload');
    assert.deepEqual(errors,[]);
    console.log('PASS: editable inspection pickers, serialized latest pair, stale response/context discarded, bounded 429 retry, generic sanitized uploads, prediction lock.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
