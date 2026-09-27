const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
 const page=await browser.newPage();
 await page.setContent('<main id="results"></main>');
 await page.addScriptTag({path:path.join(__dirname,'../static/comparisons.js')});
 await page.evaluate(()=>{
   window.makeResult=(value)=>({saved_run_id:'run-'+value,downloads:[{url:'/base'}],summary:{reference:{local_GI:value},predicted:{local_GI:value+1},max_display_Gy:30},metrics:[{number:1,role:'target',roi:'Target 1',prescription_Gy:20,predicted:{local_inverse_CI:1.2,D98_Gy:19},reference:{local_inverse_CI:value,D98_Gy:18}},{number:2,role:'target',roi:'Target 2',prescription_Gy:22,predicted:{local_inverse_CI:null},reference:{local_inverse_CI:null}}],dvhs:[{number:1,predicted:[[0,100]],reference:[[value,100]]}],slices:{shape:[1,1,1],spacing_mm:1,indices:[0],z_mm:[0],origin_lps_mm:[0,0,0],predicted:[[[25]]],reference:[[[value]]],isodose_paths:{predicted:[1],reference:[value]}},ring_benchmark:{rows:[{reference_auc_cc:value}]}});
   window.calls=[];window.forgotten=0;window.prepared=[];window.locks=[];window.selected=[];
   window.fixed={structure:new File(['SECRET_STRUCT'],'patient-struct.dcm'),targets:[1,2],targetPrescriptions:{1:20,2:22},prescription:20,sigma:1,calibrated:true};
   window.DosePrivacy={prepare:async(s,d)=>{prepared.push([s.name,d.name]);return {structure:new File(['CLEAN_STRUCT'],'bad-name.dcm'),dose:new File(['CLEAN_DOSE'],'bad-name.dcm'),context:{}};},forget:()=>forgotten++};
   window.DoseComparisons.configure({canAdd:()=>true,snapshot:()=>fixed,busy:(v)=>locks.push(v),activate:data=>{selected.push(data);DoseComparisons.setBase(data);},request:async(url,opts)=>{calls.push([...opts.body.entries()].map(([k,v])=>[k,v instanceof File?{name:v.name}:v]));if(calls.length===2)throw new Error('PRIVATE_RAW_ERROR');return makeResult(calls.length+2);}});
   DoseComparisons.setBase(makeResult(1));
   fixed.targets.push(99);fixed.targetPrescriptions[1]=99;
 });
 await page.evaluate(()=>DoseComparisons.add([new File(['SECRET_1'],'patient-a.dcm'),new File(['SECRET_2'],'patient-b.dcm'),new File(['SECRET_3'],'patient-c.dcm')]));
 assert.equal(await page.evaluate(()=>forgotten),3);
 assert.equal(await page.locator('#comparison-source option').count(),3);
 assert((await page.locator('[role=status]').textContent()).includes('1 Originalplan'));
 const calls=await page.evaluate(()=>calls);
 for(const entries of calls){const f=Object.fromEntries(entries);assert.equal(f.structure.name,'RTSTRUCT.dcm');assert.equal(f.dose.name,'RTDOSE.dcm');assert.equal(f.targets,'[1,2]');assert.equal(f.target_prescriptions,'{"1":20,"2":22}');assert.equal(f.prescription,'20');assert.equal(f.regularization_sigma_mm,'1');assert.equal(f.calibrated,'true');assert.equal(f.fractions,'1');}
 assert(!JSON.stringify(calls).includes('patient'));
 assert.equal(await page.locator('.comparison-plot').count(),2);
 assert.equal(await page.locator('.comparison-plot').nth(1).locator('circle').count(),0,'null metrics never become zero');
 assert.equal(await page.locator('.comparison-plot').nth(0).locator('circle').count(),4);
 assert.equal(await page.locator('.comparison-overview circle').count(),4,'overview excludes all missing values');
 assert.equal(await page.locator('#dose-comparisons details').getAttribute('open'),null,'small multiples are collapsed');
 assert.equal(await page.locator('#results > :first-child').getAttribute('id'),'comparison-active-bar');
 await page.selectOption('#comparison-active-source','2');
 assert.equal(await page.locator('#comparison-source').inputValue(),'2','top selector mirrors active source');
 await page.evaluate(()=>{window.DoseI18n={language:'en'};window.dispatchEvent(new Event('dose-preferences'));});
 assert.equal(await page.locator('#dose-comparisons h2').textContent(),'Original plans and per-target metrics');
 assert.equal(await page.locator('.comparison-overview figcaption').textContent(),'All-target overview');
 assert((await page.locator('.comparison-overview svg').textContent()).includes('Targets (categorical)'));
 assert((await page.locator('.comparison-overview svg').textContent()).includes('Local inverse CI'));
 assert((await page.locator('#comparison-active-bar label').textContent()).includes('Active reference'));
 assert.equal(await page.locator('#comparison-active-source option').nth(2).textContent(),'Original plan 3');
 await page.selectOption('#comparison-metric','D98_Gy');
 assert((await page.locator('.comparison-overview svg').textContent()).includes('D98 [Gy]'));
 await page.evaluate(()=>{DoseI18n.language='de';window.dispatchEvent(new Event('dose-preferences'));selected.length=0;});
 await page.selectOption('#comparison-source','1');
 assert.deepEqual(await page.evaluate(()=>({value:selected[0].slices.reference[0][0][0],predicted:selected[0].slices.predicted[0][0][0],summary:selected[0].summary.predicted.local_GI,id:selected[0].saved_run_id,download:selected[0].downloads[0].url})),{value:3,predicted:25,summary:4,id:'run-1',download:'/base'});
 assert.equal(await page.locator('#comparison-source option').count(),3,'activation does not reset extras');
 await page.evaluate(()=>DoseComparisons.add([new File(['x'],'a'),new File(['x'],'b')]));
 assert.equal(await page.evaluate(()=>calls.length),3,'bounded additions');
 await page.evaluate(()=>{DoseComparisons.reset();DoseComparisons.setBase(makeResult(1));window.releasePrepare=null;DosePrivacy.prepare=async()=>{await new Promise(resolve=>releasePrepare=resolve);return {structure:new File(['CLEAN'],'a'),dose:new File(['CLEAN'],'b'),context:{}};};window.inflight=DoseComparisons.add([new File(['SECRET'],'private')]);});
 await page.waitForFunction(()=>releasePrepare!==null);
 await page.evaluate(async()=>{DoseComparisons.reset();releasePrepare();await inflight;});
 assert.equal(await page.locator('#dose-comparisons').count(),0);
 assert.equal(await page.evaluate(()=>calls.length),3,'reset blocks stale uploads');
 assert.equal(await page.evaluate(()=>forgotten),4,'forget even when reset during prepare');
 await page.evaluate(()=>{document.documentElement.dataset.theme='dark';DoseComparisons.setBase(makeResult(1));});
 assert.equal(await page.locator('.comparison-overview circle').first().getAttribute('fill'),'#75bcff');
 for(const flag of ['demo','summary','source']){
   await page.evaluate(flag=>{const data=makeResult(1);if(flag==='demo')data.demo=true;else if(flag==='summary')data.summary.synthetic_demo=true;else data.source='synthetic';DoseComparisons.setBase(data);},flag);
   assert.equal(await page.locator('#comparison-files').count(),0,'demo cannot reuse previous upload snapshot');
   await page.evaluate(()=>DoseComparisons.add([new File(['SECRET'],'private')]));
 }
 assert.equal(await page.evaluate(()=>calls.length),3,'demo never uploads stale structures');
 console.log('Comparison privacy, fixed prescription, partial failure, source selection, missing values, bounds and reset checks passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
