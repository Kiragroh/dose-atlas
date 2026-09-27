/* Admission only: DICOM payloads stay in the browser until a slot is ready. */
(() => {
  let enabled=false, api, timer=null, job=null, latest=null, reading=false, cancelled=new Set();
  const $=id=>document.getElementById(id);
  const en=()=>window.DoseI18n.language==='en';
  const text=(de,english)=>en()?english:de;
  const sleep=()=>new Promise(resolve=>setTimeout(resolve,1500));
  function draw(data=latest) {
    if(!data)return;
    latest=data;
    $('queue-panel').hidden=!enabled;
    $('queue-clear').hidden=!data.is_admin;
    $('queue-admin-note').hidden=!data.is_admin;
    const totals=text(`${data.waiting||0} wartend · ${data.active||0} aktiv`,`${data.waiting||0} waiting · ${data.active||0} active`);
    let status=totals;
    if(job?.state==='waiting')status=text(`Warteliste: Position ${job.position}. Dateien bleiben bis zur Freigabe im Browser. `,`Waiting list: position ${job.position}. Files stay in the browser until a slot is ready. `)+totals;
    else if(job?.state==='ready')status=text('Platz freigegeben. Anonymisierte Dateien werden übertragen. ','Slot ready. Transferring anonymized files. ')+totals;
    else if(job?.state==='running')status=text('Upload / Serververarbeitung läuft. Ergebnis wird abgewartet. ','Upload / server processing is active. Waiting for the result. ')+totals;
    $('queue-status').textContent=status;
    if(job && $('status'))$('status').textContent=job.state==='waiting'?text('Wartet auf freien Serverplatz …','Waiting for an available server slot …'):text('Anonymisierter Upload / Serververarbeitung …','Anonymized upload / server processing …');
  }
  async function refresh(){
    if(!enabled || reading)return;
    reading=true;
    try {const data=await api('/api/queue'); if(enabled)draw(data);}
    catch {if(enabled)$('queue-status').textContent=text('Wartelistenstatus derzeit nicht erreichbar.','Waiting-list status currently unavailable.');}
    finally{reading=false;}
  }
  function configure(options){
    api=options.request;enabled=options.enabled;
    $('queue-panel').hidden=!enabled;
    clearInterval(timer);timer=null;
    if(enabled){void refresh();timer=setInterval(refresh,5000);}
  }
  async function run(url,options,request){
    let ticket=null, polling=null, pollBusy=false;
    try {
      ticket=await request('/api/queue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:url.endsWith('inspect')?'inspect':'predict'}),signal:options.signal});
      if(!ticket.id)throw new Error(text('Keine Wartelistenfreigabe erhalten.','No queue admission received.'));
      job=ticket;draw({...latest,...ticket});
      while(job.state==='waiting'){
        if(cancelled.has(ticket.id))throw new DOMException('Aborted','AbortError');
        await sleep();
        if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
        job=await request('/api/queue/'+encodeURIComponent(ticket.id),{signal:options.signal});draw({...latest,...job});
      }
      if(cancelled.has(ticket.id) || options.signal?.aborted)throw new DOMException('Aborted','AbortError');
      if(job.state!=='ready')throw new Error(text('Der wartende Auftrag wurde entfernt oder ist abgelaufen. Bitte erneut starten.','The waiting job was removed or expired. Please start again.'));
      // Poll actual admission state while the independent payload request runs.
      polling=setInterval(async()=>{
        if(pollBusy)return;pollBusy=true;
        try {const data=await request('/api/queue/'+encodeURIComponent(ticket.id));if(job?.id===ticket.id){job=data;draw({...latest,...data});}}
        catch {if(job?.id===ticket.id)$('queue-status').textContent=text('Statusabfrage unterbrochen; die Antwort auf den Auftrag steht noch aus.','Status check interrupted; the job response is still pending.');}
        finally{pollBusy=false;}
      },2000);
      const headers=new Headers(options.headers||{});headers.set('X-Dose-Job',ticket.id);
      return await request(url,{...options,headers});
    }finally{
      clearInterval(polling);if(ticket?.id)cancelled.delete(ticket.id);job=null;
      if(ticket?.id)try{await request('/api/queue/'+encodeURIComponent(ticket.id),{method:'DELETE'});}catch{}
      void refresh();
    }
  }
  $('queue-clear').addEventListener('click',async()=>{
    $('queue-clear').disabled=true;
    try {const result=await api('/api/queue/clear',{method:'POST'});await refresh();$('queue-status').textContent=text(`${result.cleared} wartende Aufträge entfernt. Laufende Berechnungen bleiben aktiv.`,`${result.cleared} waiting jobs removed. Running calculations remain active.`);}
    catch {$('queue-status').textContent=text('Warteliste konnte nicht geleert werden. Berechtigung und Verbindung prüfen.','Could not clear the queue. Check permission and connection.');}
    finally{$('queue-clear').disabled=false;}
  });
  window.addEventListener('dose-preferences',()=>draw());
  window.addEventListener('pagehide',()=>{clearInterval(timer);enabled=false;});
  function cancelPending(){
    if(job && ['waiting','ready'].includes(job.state)){
      cancelled.add(job.id);
      void api('/api/queue/'+encodeURIComponent(job.id),{method:'DELETE'}).catch(()=>{});
    }
  }
  window.DoseQueue={configure,run,refresh,cancelPending};
})();
