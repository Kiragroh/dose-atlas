// Actual application + privacy Worker, synthetic identifiers only, loopback only.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const zlib = require('node:zlib');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
  const input = JSON.parse(zlib.gunzipSync(fs.readFileSync(0)));
  const uploads = [], requests = [], errors = [];
  const server = http.createServer(async (req,res) => {
    const chunks=[]; for await (const chunk of req) chunks.push(chunk);
    const body=Buffer.concat(chunks); requests.push({url:req.url,method:req.method,headers:req.headers,body});
    const json = value => {res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};
    if(req.url==='/api/auth/session') return json({enabled:false,authenticated:false});
    if(req.url==='/api/inspect') {uploads.push(body);return json({rois:[{number:1,name:'PTV 1'}],suggested_targets:[1],warnings:[]});}
    if(req.url==='/api/predict') {uploads.push(body);return json({summary:{prescription_Gy:20},metrics:[],dvhs:[],targets3d:[],downloads:[{label:'RTDOSE',url:'/downloads/prediction.dcm'}]});}
    if(req.url==='/downloads/prediction.dcm') {res.setHeader('Content-Type','application/dicom');return res.end(Buffer.from(input.predicted,'base64'));}
    if(req.url.startsWith('/api/')) return json({});
    const name=req.url==='/'?'index.html':path.basename(req.url.split('?')[0]);
    const file=path.join(__dirname,'../static',name);
    if(!fs.existsSync(file)){res.statusCode=404;return res.end();}
    res.setHeader('Content-Type',name.endsWith('.html')?'text/html':name.endsWith('.css')?'text/css':'application/javascript');res.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();let workers=0;
    page.on('worker',()=>workers++);page.on('pageerror',error=>errors.push(error.message));
    const origin=`http://127.0.0.1:${server.address().port}`;
    await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    await page.goto(origin);
    await page.waitForFunction(()=>!document.getElementById('structure-file').disabled);
    for(const kind of ['structure','dose']) await page.locator(`#${kind}-file`).setInputFiles({name:`PRIVATE_${kind}.dcm`,mimeType:'application/dicom',buffer:Buffer.from(input[kind],'base64')});
    await page.locator('#process-button').click();
    await page.waitForFunction(()=>document.getElementById('roi-list').textContent.includes('PTV 1')&&!document.getElementById('prescription').disabled);
    await page.locator('#prescription').fill('20');await page.locator('#predict-button').click();
    await page.locator('#original-export-option').waitFor({state:'visible'});
    assert.equal(await page.locator('#original-export-toggle').isChecked(),false);
    assert.equal(await page.locator('#original-export-button').isVisible(),false);
    assert(!requests.some(r=>r.url==='/downloads/prediction.dcm'));
    // Capture the locally generated Blob in RAM, without writing identifiers to disk.
    await page.evaluate(()=>{const create=URL.createObjectURL.bind(URL);URL.createObjectURL=blob=>{window.localExport=blob;return create(blob);};});
    await page.locator('#original-export-toggle').check();
    await page.locator('#original-export-button').click();
    await page.waitForFunction(()=>Boolean(window.localExport));
    const restored=await page.evaluate(async()=>{const bytes=new Uint8Array(await window.localExport.arrayBuffer());return btoa(String.fromCharCode(...bytes));});
    await page.locator('#original-export-toggle').uncheck();
    assert.equal(await page.locator('#original-export-button').isVisible(),false);
    assert(workers>=1);assert(uploads.length>=2);
    for(const request of requests) for(const marker of input.forbidden) {
      assert(!request.body.includes(Buffer.from(marker)),'Original identifier appeared in network body');
      assert(!decodeURIComponent(request.url).includes(marker),'Original identifier appeared in URL');
      assert(!JSON.stringify(request.headers).includes(marker),'Original identifier appeared in headers');
    }
    for(const body of uploads) {assert(body.includes(Buffer.from('filename="RTSTRUCT.dcm"')));assert(body.includes(Buffer.from('ANON')));}
    assert(uploads.at(-1).includes(Buffer.from('filename="RTDOSE.dcm"')));
    const downloadRequests=requests.filter(r=>r.url==='/downloads/prediction.dcm');
    assert.equal(downloadRequests.length,1);assert.equal(downloadRequests[0].method,'GET');assert.equal(downloadRequests[0].body.length,0);
    assert.deepEqual(errors,[]);
    process.stdout.write(zlib.gzipSync(JSON.stringify({restored,uploads:uploads.length,downloadRequests:downloadRequests.length})));
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
