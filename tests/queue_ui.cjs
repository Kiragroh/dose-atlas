const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{const browser=await chromium.launch();try{
 const page=await browser.newPage();
 await page.setContent('<section id="queue-panel"><p id="queue-status"></p><button id="queue-clear"></button><p id="queue-admin-note"></p></section>');
 await page.evaluate(()=>{window.DoseI18n={language:'de'};window.ready=false;window.posts=[];window.release=null;window.admin=false;window.deleted=0;window.cleared=0;window.raw=async(url,opts={})=>{
  if(url==='/api/queue/clear'){cleared++;return {cleared:2};}
  if(url==='/api/queue'&&opts.method==='POST')return {id:'test-ticket',state:'waiting',position:2,waiting:2,active:1};
  if(url==='/api/queue')return {waiting:2,active:1,is_admin:admin};
  if(url==='/api/queue/test-ticket'){if(opts.method==='DELETE'){deleted++;return {};}return {id:'test-ticket',state:ready?'ready':'waiting',position:2,waiting:2,active:1};}
  posts.push({url,ticket:opts.headers.get('X-Dose-Job')});await new Promise(resolve=>release=resolve);return {success:true};
 };});
 await page.addScriptTag({path:path.join(__dirname,'../static/queue.js')});
 await page.evaluate(()=>{DoseQueue.configure({enabled:true,request:raw});window.done=DoseQueue.run('/api/inspect',{method:'POST',body:'SANITIZED'},raw);});
 await page.waitForFunction(()=>document.getElementById('queue-status').textContent.includes('Position 2'));
 assert.equal(await page.evaluate(()=>posts.length),0,'no upload while waiting');
 assert.equal(await page.locator('#queue-clear').isHidden(),true);
 await page.evaluate(()=>ready=true);await page.waitForFunction(()=>Boolean(release));
 assert.deepEqual(await page.evaluate(()=>posts),[{url:'/api/inspect',ticket:'test-ticket'}]);
 await page.evaluate(async()=>{release();await done;});
 assert.equal(await page.evaluate(()=>deleted),1);
 await page.evaluate(async()=>{admin=true;await DoseQueue.refresh();});
 assert.equal(await page.locator('#queue-clear').isVisible(),true);
 await page.locator('#queue-clear').click();assert.equal(await page.evaluate(()=>cleared),1);
 await page.evaluate(()=>{ready=false;window.cancelledRun=DoseQueue.run('/api/predict',{method:'POST'},raw).catch(e=>e.name);});
 await page.waitForFunction(()=>document.getElementById('queue-status').textContent.includes('Position 2'));
 await page.evaluate(()=>DoseQueue.cancelPending());
 assert.equal(await page.evaluate(()=>cancelledRun),'AbortError');
 assert.equal(await page.evaluate(()=>posts.length),1,'cancelled waiting job never uploads');
 await page.evaluate(()=>{DoseI18n.language='en';window.dispatchEvent(new Event('dose-preferences'));});
 assert((await page.locator('#queue-status').textContent()).includes('waiting'));
 console.log('PASS: explicit admission before upload, ticket header, admin control, pending cancellation and English.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
