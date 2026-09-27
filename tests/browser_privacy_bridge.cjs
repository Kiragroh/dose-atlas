// Local stdin/stdout transport only; never log filenames or original metadata.
const fs = require('node:fs');
const zlib = require('node:zlib');
globalThis.File ??= require('node:buffer').File;
require('../static/dicom-privacy.js');
(async()=>{
 const input=JSON.parse(zlib.gunzipSync(fs.readFileSync(0)).toString('utf8'));
 if(process.env.DOSE_ATLAS_BROWSER_TEST==='1'){
  const {chromium}=require('playwright');const http=require('node:http');
  const script=fs.readFileSync(require('node:path').join(__dirname,'../static/dicom-privacy.js'));
  const server=http.createServer((req,res)=>{res.setHeader('Content-Type',req.url==='/dicom-privacy.js'?'application/javascript':'text/html');res.end(req.url==='/dicom-privacy.js'?script:'<script src="/dicom-privacy.js"></script>');});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});
  try{const page=await browser.newPage();let workers=0;page.on('worker',()=>workers++);await page.goto(`http://127.0.0.1:${server.address().port}`);
   const output=await page.evaluate(async input=>{
    const file=k=>input[k]?new File([Uint8Array.from(atob(input[k]),c=>c.charCodeAt(0))],'input.dcm'):null;
    const encode=async blob=>{const bytes=new Uint8Array(await blob.arrayBuffer());let text='';for(let p=0;p<bytes.length;p+=32768)text+=String.fromCharCode(...bytes.subarray(p,p+32768));return btoa(text);};
    let ticks=0;const timer=setInterval(()=>ticks++,1);const r=await DosePrivacy.prepare(file('structure'),file('dose'));clearInterval(timer);
    const restored=r.dose?await DosePrivacy.restoreOriginalCase(file('predicted')||r.dose,r.context):null;
    const result={structure:await encode(r.structure),dose:r.dose?await encode(r.dose):null,restored:restored?await encode(restored):null,names:[r.structure.name,r.dose?.name]};
    if(!ticks||!DosePrivacy.forget(r.context))throw Error('WORKER_LIFECYCLE');let rejected=false;try{await DosePrivacy.restoreOriginalCase(r.dose,r.context);}catch{rejected=true;}if(!rejected)throw Error('CONTEXT_NOT_RELEASED');return result;
   },input);
   if(workers!==1)throw Error('WORKER_NOT_USED');process.stdout.write(zlib.gzipSync(JSON.stringify(output),{level:1}));
  }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}return;
 }
 const file=k=>input[k]?new File([Buffer.from(input[k],'base64')],'input.dcm'):null;
 const r=await DosePrivacy.prepare(file('structure'),file('dose'));
 const encoded=async b=>Buffer.from(await b.arrayBuffer()).toString('base64');
 const restored=r.dose?await DosePrivacy.restoreOriginalCase(file('predicted')||r.dose,r.context):null;
 process.stdout.write(zlib.gzipSync(JSON.stringify({structure:await encoded(r.structure),dose:r.dose?await encoded(r.dose):null,restored:restored?await encoded(restored):null,names:[r.structure.name,r.dose?.name]}),{level:1}));
})().catch(()=>{process.stderr.write('PRIVACY_PARSE_FAILED');process.exitCode=1;});
