const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch();
 try {
  const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('http://dose.test/**',async route=>{
   const pathname=new URL(route.request().url()).pathname;
   if(pathname.startsWith('/api/'))return route.fulfill({json:pathname==='/api/auth/session'?{enabled:false}: {}});
   const name=pathname==='/'?'index.html':path.basename(pathname);
   return route.fulfill({body:fs.readFileSync(path.join(__dirname,'../static',name)),contentType:name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'application/javascript'});
  });
  await page.goto('http://dose.test');await page.waitForFunction(()=>!document.getElementById('structure-file').disabled);
  await page.evaluate(()=>{
   renderInspection({rois:[{number:1,name:'PTV_A'},{number:2,name:'PTV_B'},{number:3,name:'GTV_A'}],prescriptions:[{number:1,eligible:true,suggested_Gy:20},{number:2,eligible:true,suggested_Gy:21}]});state.inspected=true;updateButton();
  });
  assert.deepEqual(await page.evaluate(()=>selectedTargets()),[1,2],'prefer PTVs over GTVs');
  await page.locator('#proposal-area > summary').click();
  await page.locator('#confirm-proposals').click();
  assert.deepEqual(await page.evaluate(()=>selectedPrescriptionMap()),{'1':20,'2':21});
  assert.equal(await page.locator('#predict-button').isEnabled(),true,'mixed Rx can be submitted');
  await page.locator('#prescription').fill('19');
  assert.deepEqual(await page.evaluate(()=>selectedPrescriptionMap()),{'1':20,'2':21},'common edit preserves overrides');
  await page.locator('#apply-prescription-all').click();
  assert.deepEqual(await page.evaluate(()=>selectedPrescriptionMap()),{'1':19,'2':19});
  await page.locator('#target-prescriptions-area > summary').click();
  await page.locator('#target-prescriptions input[data-roi="2"]').fill('21');
  assert.deepEqual(await page.evaluate(()=>selectedPrescriptionMap()),{'1':19,'2':21});
  await page.locator('#target-prescriptions input[data-roi="2"]').fill('101');
  assert.equal(await page.locator('#predict-button').isDisabled(),true,'technical upper bound enforced');
  await page.evaluate(()=>{
   targetPrescriptions.clear();renderInspection({rois:[{number:7,name:'GTV_A'},{number:8,name:'Brain'}]},true);updateButton();
  });
  assert.deepEqual(await page.evaluate(()=>selectedTargets()),[7],'GTV fallback');
  assert.equal(await page.locator('#roi-list .roi-option small').textContent(),'GTV','fallback badge uses actual target kind');
  assert.equal(await page.locator('#target-prescriptions input').isDisabled(),true,'test cases stay read-only');
  assert.equal(await page.locator('#prediction-variant').isDisabled(),true,'read-only test plan cannot imply a different variant');
  await page.evaluate(()=>{
   const targets=[{number:1,name:'PTV_A',center_lps_mm:[0,0,0],radius_mm:3,prescription_Gy:20},{number:2,name:'PTV_B',center_lps_mm:[10,0,0],radius_mm:3,prescription_Gy:21}];
   const dvhs=targets.map(target=>({roi:target.name,roi_number:target.number,prescription_Gy:target.prescription_Gy,predicted:[[0,100],[19,100],[19,50],[22,0]],reference:[[0,100],[18,100],[18,40],[23,0]]}));
   renderResult({summary:{prescription_Gy:20,mixed_prescriptions:true,prescription_range_Gy:[20,21]},targets3d:targets,dvhs,metrics:targets.map(target=>({roi:target.name,role:'target',prescription_Gy:target.prescription_Gy}))},false);
  });
  assert.equal(await page.evaluate(()=>visibleDVHs().length),2,'default all target DVHs');
  await page.locator('#target-list [data-target="2"]').click();
  assert.equal(await page.evaluate(()=>visibleDVHs()[0].roi),'PTV_B');
  assert((await page.locator('#dvh-rx-label').innerText()).includes('21'));
  assert.equal(await page.locator('#dvh-legend .legend-item').count(),2,'separate prediction/reference legend');
  await page.locator('#dvh-select').selectOption('all-targets');
  assert.equal(await page.evaluate(()=>visibleDVHs().length),2);
  const diagonal=await page.evaluate(()=>{
   const canvas=document.getElementById('dvh-canvas').getContext('2d'),originalMove=canvas.moveTo.bind(canvas),originalLine=canvas.lineTo.bind(canvas);let previous=null,diagonal=0;
   canvas.moveTo=(x,y)=>{previous=[x,y];originalMove(x,y)};
   canvas.lineTo=(x,y)=>{if(['predicted','reference'].map(comparisonColor).includes(canvas.strokeStyle)&&previous&&Math.abs(x-previous[0])>.001&&Math.abs(y-previous[1])>.001)diagonal++;previous=[x,y];originalLine(x,y)};
   drawDVH();return diagonal;
  });
  assert.equal(diagonal,0,'empirical curves use horizontal/vertical steps, never diagonal smoothing');
  await page.evaluate(()=>{targetPrescriptions.clear();renderInspection({rois:Array.from({length:24},(_,i)=>({number:i+1,name:`PTV ${i+1}`}))});state.inspected=true;updateButton()});
  await page.locator('#prescription').fill('20');
  assert.equal(await page.locator('#target-prescriptions-area').getAttribute('open'),null);
  assert.equal(await page.locator('#target-selection-details').getAttribute('open'),null);
  assert((await page.locator('#target-input-summary').innerText()).includes('24'));
  await page.locator('#target-prescriptions-area > summary').click();
  assert.equal(await page.locator('#target-prescriptions input').count(),24);
  await page.locator('#target-prescriptions input').last().fill('22');
  assert((await page.locator('#target-input-summary').innerText()).includes('20'));
  assert((await page.locator('#target-input-summary').innerText()).includes('22'));
  await page.locator('#target-prescriptions-area > summary').click();
  await page.locator('#language-select').selectOption('en');
  assert((await page.locator('#target-input-summary').innerText()).includes('targets selected'));
  await page.locator('#theme-toggle').click();
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('.setup').screenshot({path:'private/compact-input-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('.setup').screenshot({path:'private/compact-input-mobile.png'});
  assert.deepEqual(errors,[]);console.log('PASS: target-specific proposals/common override/apply-all/bounds/PTV-GTV preference/read-only; selected DVH, target sync, own Rx, line legend, empirical steps.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});

