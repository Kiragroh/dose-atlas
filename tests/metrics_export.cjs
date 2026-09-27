const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const os=require('node:os');

function fixture(label,value){return {label,data:{PatientID:'PRIVATE-ID',filename:'PRIVATE-FILE.dcm',summary:{synthetic_demo:true,prediction_regularization_sigma_mm:1,prescription_range_Gy:[18,24],fractions:1,reference_coverage_pct:99,PatientID:123456789,predicted:{mean_local_inverse_CI:1.4,mean_local_inverse_CI_n:1,V12_domain_cc:0},reference:{mean_local_inverse_CI:null,mean_local_inverse_CI_n:0,V12_domain_cc:2}},metrics:[{roi:'=ÄÖ & <Target>',number:7,role:'target',prescription_Gy:24,volume_cc:1.5,local_coverage_pct:99,local_region_cc:23,predicted:{D98_Gy:value,Dmean_Gy:25,local_inverse_CI:0,local_GI:null,local_V12_cc:0,uid:123456789},reference:{D98_Gy:18,Dmean_Gy:19,local_inverse_CI:1.8,local_GI:2,local_V12_cc:3}},{roi:'Hirnstamm',role:'organ',prescription_Gy:24,volume_cc:5,predicted:{D98_Gy:0},reference:{D98_Gy:null}}]}};}
(async()=>{
 const comparisons=[fixture('Originalplan 1',19),fixture('Originalplan 2',20)],original=JSON.stringify(comparisons);
 const context={window:{},TextEncoder,Uint8Array,Uint32Array,DataView,Blob,setTimeout,URL};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/metrics-export.js'),'utf8'),context);
 const blob=await context.window.DoseMetricsExport.build(comparisons);
 assert.equal(blob.type,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
 assert.equal(JSON.stringify(comparisons),original);
 const folder=fs.mkdtempSync(path.join(os.tmpdir(),'dose-metrics-'));
 try{
   const file=path.join(folder,'metrics.xlsx');fs.writeFileSync(file,Buffer.from(await blob.arrayBuffer()));
   const checked=spawnSync(process.env.PYTHON||'python',[path.join(__dirname,'test_metrics_export.py'),file],{encoding:'utf8'});
   assert.equal(checked.status,0,checked.stdout+'\n'+checked.stderr);process.stdout.write(checked.stdout);
   if(process.env.TEST_BROWSER==='1'){
     const {chromium}=require('playwright'),browser=await chromium.launch({headless:true});
     try{
       const page=await browser.newPage({acceptDownloads:true});let requests=0;page.on('request',()=>requests++);
       await page.setContent('<body></body>');await page.addScriptTag({path:path.join(__dirname,'../static/metrics-export.js')});
       const pending=page.waitForEvent('download');await page.evaluate(c=>window.DoseMetricsExport.download(c),comparisons);
       const download=await pending;assert.equal(download.suggestedFilename(),'Dose-Atlas-Metriken.xlsx');
       await download.saveAs(path.join(folder,'browser.xlsx'));assert.equal(requests,0);
       const browserCheck=spawnSync(process.env.PYTHON||'python',[path.join(__dirname,'test_metrics_export.py'),path.join(folder,'browser.xlsx')],{encoding:'utf8'});
       assert.equal(browserCheck.status,0,browserCheck.stderr);console.log('Browser download + no network requests: passed');
     }finally{await browser.close();}
   }
 }finally{fs.rmSync(folder,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exit(1);});
