const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch();
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://dose.test/**',route=>{
   const pathname=new URL(route.request().url()).pathname;
   if(pathname==='/api/model')return route.fulfill({json:{validation:{},spatial_regularization:JSON.parse(fs.readFileSync(path.join(__dirname,'../artifacts/continuity_validation.json'),'utf8'))}});
   if(pathname.startsWith('/api/'))return route.fulfill({json:pathname==='/api/auth/session'?{enabled:false}:{}});
   const name=pathname==='/'?'index.html':path.basename(pathname);
   return route.fulfill({body:fs.readFileSync(path.join(__dirname,'../static',name)),contentType:name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'application/javascript'});
  });
  await page.goto('http://dose.test'); await page.waitForFunction(()=>!document.getElementById('structure-file').disabled);
  await page.waitForFunction(()=>document.getElementById('model-evidence').textContent.includes('Target-Voxel-MAE'));
  assert((await page.locator('#model-evidence').textContent()).includes('Validierung des Basismodells'));
  assert((await page.locator('#model-evidence').textContent()).includes('schlechterer voxelweiser'));
  await page.evaluate(()=>{
   const targets=[{number:1,name:'PTV_A',center_lps_mm:[0,0,0],radius_mm:3},{number:2,name:'PTV_B',center_lps_mm:[40,0,0],radius_mm:3}];
   const grid=Array.from({length:20},(_,y)=>Array.from({length:30},(_,x)=>x+y));
   renderResult({summary:{prescription_Gy:20},targets3d:targets,dvhs:targets.map(t=>({roi:t.name,roi_number:t.number,predicted:[[0,100],[20,100],[21,0]]})),metrics:targets.map(t=>({roi:t.name,role:'target'})),slices:{predicted:[grid,grid],target:[grid.map(r=>r.map(v=>v>20?1:0))],target_contours:[[[[0,0,0],[3,1,0],[4,4,0],[0,3,0]]],[]],z_mm:[0,2],spacing_mm:[2,2,2],origin_lps_mm:[0,0,0]}},false);
  });
  assert((await page.locator('#dvh-method-note').innerText()).startsWith('Empirische'),'old result does not claim regularization');
  await page.evaluate(()=>{state.result.summary.prediction_regularization_sigma_mm=.5;renderResult(state.result,false)});
  assert((await page.locator('#dvh-method-note').innerText()).includes('0,5 mm'),'regularization note gated by result metadata');
  await page.evaluate(()=>{state.result.summary.prediction_regularization_sigma_mm=1;renderDVHMethodNote()});
  assert((await page.locator('#dvh-method-note').innerText()).includes('1,0 mm'),'current sigma rendered');
  await page.evaluate(()=>{state.result.summary.prediction_regularization_sigma_mm=0;renderDVHMethodNote()});
  assert((await page.locator('#dvh-method-note').innerText()).includes('Basismodell ohne'),'raw variant is not described as regularized');
  const contour=await page.evaluate(()=>{
   const c=$('slice-canvas').getContext('2d'),move=c.moveTo.bind(c),line=c.lineTo.bind(c),close=c.closePath.bind(c);let paths=[];
   c.moveTo=(x,y)=>{if(c.strokeStyle==='#f5fff0')paths.push(['M',x,y]);move(x,y)};
   c.lineTo=(x,y)=>{if(c.strokeStyle==='#f5fff0')paths.push(['L',x,y]);line(x,y)};
   c.closePath=()=>{if(c.strokeStyle==='#f5fff0')paths.push(['Z']);close()};
   setSlice(0);const native=paths.slice();paths=[];
   delete state.result.slices.target_contours;drawSlice();const fallback=paths.slice();
   c.moveTo=move;c.lineTo=line;c.closePath=close;return {native,fallback};
  });
  assert.deepEqual(contour.native.map(p=>p[0]),['M','L','L','L','Z'],'native contour vertices passed through without invented curve points');
  assert(contour.native[1][1]!==contour.native[0][1] && contour.native[1][2]!==contour.native[0][2],'native oblique edge remains oblique');
  assert(contour.fallback.some((p,i)=>p[0]==='L'&&p[1]!==contour.fallback[i-1][1]&&p[2]!==contour.fallback[i-1][2]),'legacy masks use marching squares diagonal edges');
  const isodose=await page.evaluate(()=>{
   const c=$('slice-canvas').getContext('2d'),move=c.moveTo.bind(c),line=c.lineTo.bind(c),close=c.closePath.bind(c);let paths=[];
   const level=isoLevels[0],style=isoColors[0];
   c.moveTo=(x,y)=>{if(c.strokeStyle===style)paths.push(['M',x,y]);move(x,y)};
   c.lineTo=(x,y)=>{if(c.strokeStyle===style)paths.push(['L',x,y]);line(x,y)};
   c.closePath=()=>{if(c.strokeStyle===style)paths.push(['Z']);close()};
   $('isodose-levels').querySelectorAll('input').forEach(i=>i.checked=Number(i.value)===level);
   state.result.slices.isodose_paths={predicted:[{[String(level)+'.0']:[[[0,0,0],[3.25,1.5,0],[7.5,4,0]]]}],reference:null};
   setSlice(0);const full=paths.slice();paths=[];
   state.result.slices.isodose_paths.predicted[0][String(level)+'.0']=[];drawSlice();const empty=paths.slice();paths=[];
   state.result.slices.isodose_paths.predicted[0]=null;drawSlice();const fallback=paths.slice();
   c.moveTo=move;c.lineTo=line;c.closePath=close;return {full,empty,fallback};
  });
  assert.deepEqual(isodose.full.map(p=>p[0]),['M','L','L'],'full-grid paths preserve exact vertices and open ends');
  assert.deepEqual(isodose.empty,[],'empty native level suppresses coarse fallback');
  assert(isodose.fallback.length>0,'missing native level uses legacy marching squares');
  const unchanged=await page.evaluate(()=>JSON.stringify(state.result));
  await page.locator('#head-toggle').check();
  assert.equal(await page.locator('#head-toggle-3d').isChecked(),true);
  assert.equal(await page.evaluate(()=>JSON.stringify(state.result)),unchanged,'head cannot alter data');
  await page.locator('#head-toggle-3d').uncheck();
  assert.equal(await page.locator('#head-toggle').isChecked(),false);
  const chip=page.locator('#target-list [data-target="2"]');await chip.click();
  assert.equal(await chip.getAttribute('aria-pressed'),'true');assert.equal(await page.evaluate(()=>visibleDVHs().length),1);
  await chip.click();assert.equal(await chip.getAttribute('aria-pressed'),'false');
  assert.deepEqual(await page.evaluate(()=>[state.focus,state.zoom,state.orbit.zoom,visibleDVHs().length]),[null,1,1,2]);
  await chip.click();await page.locator('#show-all-targets').click();assert.equal(await page.evaluate(()=>visibleDVHs().length),2);
  for(const kind of ['2d','3d','dvh']){
   const button=page.locator(`[data-expand-view="${kind}"]`),canvas=page.locator(kind==='2d'?'#slice-canvas':kind==='3d'?'#spatial-canvas':'#dvh-canvas');
   const before=await canvas.boundingBox();await button.click();await page.waitForFunction(()=>document.getElementById('view-dialog').open);
   assert.equal(await page.locator('#view-dialog '+(kind==='2d'?'#slice-canvas':kind==='3d'?'#spatial-canvas':'#dvh-canvas')).count(),1);
   await page.waitForTimeout(80);const after=await canvas.boundingBox();assert(after.width>before.width || after.height>before.height,'expanded canvas gets more room');
   assert.equal(await page.evaluate(id=>{const c=document.getElementById(id);return Math.abs(c.width-c.getBoundingClientRect().width*devicePixelRatio)<2},await canvas.getAttribute('id')),true,'backing canvas resized');
   if(kind==='2d'){await page.locator('#zoom-in').click();assert((await page.evaluate(()=>state.zoom))>1);await page.locator('#slice-slider').fill('1');}
   if(kind==='3d'){await page.locator('#spatial-in').click();await page.locator('#show-all-targets').click();}
   if(kind==='dvh'){await page.locator('#dvh-select').selectOption('1');assert.equal(await page.evaluate(()=>visibleDVHs()[0].roi),'PTV_B');}
   await page.keyboard.press('Escape');assert.equal(await page.locator('#view-dialog').evaluate(d=>d.open),false);assert.equal(await button.evaluate(b=>b===document.activeElement),true,'focus restored');
  }
  await page.evaluate(()=>{state.result.slices.reference=state.result.slices.predicted.map(layer=>layer.map(row=>row.map(v=>v-2)));state.mode='difference';drawSlice();});
  assert.deepEqual(await page.evaluate(()=>[-5,0,5].map(v=>differenceColor(v))),[[33,102,172],[247,247,247],[178,24,43]]);
  assert.equal(await page.locator('#difference-controls').isVisible(),true);
  assert.equal(await page.locator('#isodose-levels').isHidden(),true);
  await page.locator('#difference-range').selectOption('2');
  assert((await page.locator('#scale-max').textContent()).includes('2,0'));
  await page.locator('[data-expand-view="2d"]').click();
  assert.equal(await page.locator('#view-dialog #difference-controls').count(),1);
  await page.keyboard.press('Escape');
  await page.locator('#language-select').selectOption('en');await page.locator('#theme-toggle').click();
  assert.equal(await page.locator('[data-expand-view="2d"]').innerText(),'Enlarge 2D view');
  await page.setViewportSize({width:390,height:844});await page.locator('[data-expand-view="2d"]').click();await page.locator('#close-view').click();
  assert.equal(await page.locator('#slice-canvas').count(),1,'no cloned canvas or IDs');assert.deepEqual(errors,[]);
  console.log('PASS: target toggle/show all, independent 2D/3D/DVH dialogs, live controls, ResizeObserver backing size, Escape/focus restoration, English, theme, mobile.');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
