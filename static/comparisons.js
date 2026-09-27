/* Session-only comparisons. Original DICOM files never leave the browser. */
(() => {
  'use strict';
  const METRICS = {local_inverse_CI:['Lokaler inverser CI','1'],local_GI:['Lokaler GI','1'],local_V12_cc:['Lokales V12','cm³'],D98_Gy:['D98','Gy']};
  const COLORS=['#ba5600','#27824f','#984caa','#a33b57'];
  const dark=()=>document.documentElement.dataset.theme==='dark';
  const sourceColor=i=>dark()?['#ffc078','#73d39c','#d2a0ed','#ff9db7'][i]:COLORS[i];
  const predictionColor=()=>dark()?'#75bcff':'#277fca';
  const EN={
    'Lokaler inverser CI':'Local inverse CI','Lokaler GI':'Local GI','Lokales V12':'Local V12','Vorhersage':'Prediction','Vorh.':'Pred.',
    'nicht verfügbar':'unavailable','Dosisquelle (kategorial)':'Dose source (categorical)','Targets (kategorial)':'Targets (categorical)',
    'Originalpläne und Kennzahlen je Target':'Original plans and per-target metrics',
    'Bis zu vier Originalpläne mit identischer Targetauswahl und Verschreibung. Die gemeinsame Ansicht besteht nur in dieser Sitzung. Zusätzliche Analysen werden als getrennte Läufe gespeichert, sofern das Archiv verfügbar ist. DICOM-Downloads gehören weiterhin zur Basisanalyse.':'Up to four original plans with the same targets and prescriptions. This combined view exists only in this session. Additional analyses are saved as separate runs when archive storage is available. DICOM downloads still belong to the base analysis.',
    'Originalplan in Schnittbildern, DVH und Tabellen':'Original plan in slices, DVH and tables',
    'Aktive Referenz in Schnittbildern, DVH und Tabellen (orange)':'Active reference in slices, DVH and tables (orange)',
    'Kennzahl je Target':'Per-target metric','Originalpläne hinzufügen':'Add original plans','Weitere Originalpläne sind verfügbar.':'Additional original plans are available.',
    'Weitere Originalpläne werden lokal anonymisiert und einzeln analysiert …':'Additional original plans are being anonymized locally and analyzed individually …',
    'Punkte zeigen einzelne Dosisquellen; fehlende Werte bleiben leer (—). Die Achse beginnt bei null. Lokales V12 ist geometrisch zugeordnetes Gewebe außerhalb der Targets, ohne Hirnmaske.':'Points represent individual dose sources; missing values remain empty (—). The axis starts at zero. Local V12 measures geometrically assigned tissue outside targets, without a brain mask.',
    'Übersicht aller Targets':'All-target overview','Einzelansichten je Target':'Individual target views',
    'Punkte je Dosisquelle auf gemeinsamer Skala. Targetnummern entsprechen der Reihenfolge der Einzelansichten. Keine Verbindung über fehlende Werte.':'Points for each dose source on a common scale. Target numbers follow the order of the individual views. Missing values are not bridged.'
  };
  function t(text){
    if(window.DoseI18n?.language!=='en' || typeof text!=='string')return text;
    if(EN[text])return EN[text];
    return text.replace(/Originalplan (\d+)/g,'Original plan $1').replace(' (keine Dosis)',' (no dose)').replace(/Lokaler inverser CI/g,'Local inverse CI').replace(/Lokaler GI/g,'Local GI').replace(/Lokales V12/g,'Local V12')
      .replace(/^Weitere RTDOSE-Dateien \(noch (\d+)\)$/,'Additional RTDOSE files ($1 remaining)')
      .replace(/^Maximal (\d+) weitere Originalpläne möglich\.$/,'At most $1 additional original plans allowed.')
      .replace(/^(\d+) Originalplan\/-pläne konnten nicht sicher verglichen werden\. Erfolgreiche Vergleiche bleiben erhalten\.$/,'$1 original plan(s) could not be safely compared. Successful comparisons remain available.')
      .replace(/^Aktive Referenz: /,'Active reference: ')
      .replace('Vorhersagedosis und DICOM-Exporte bleiben aus der Basisanalyse. Tabellen verwenden die gemeinsame Dosisabdeckung mit der aktiven Referenz; die blauen Plotpunkte stammen aus der Basisanalyse. Lokale Kennzahlen und Ringdaten verlangen vollständige Abdeckung.','Predicted dose and DICOM exports remain from the base analysis. Tables use dose coverage shared with the active reference; blue plot points come from the base analysis. Local metrics and ring data require complete coverage.');
  }
  let hooks,base=null,extras=[],active=0,working=false,activating=false,epoch=0,controller=null,snapshot=null,metric='local_inverse_CI',message='';
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=t(text);if(cls)n.className=cls;return n;};
  const id=row=>row.roi_number ?? row.number ?? row.roi;
  const finite=v=>typeof v==='number' && Number.isFinite(v);
  function references(){return [{data:base,label:'Originalplan 1'},...extras];}
  function mergeReference(original,chosen) {
    if(original===chosen)return original;
    const rows=(key)=>(original[key]||[]).map(row=>({...row,...((chosen[key]||[]).find(other=>id(other)===id(row)) || {predicted:null,reference:null})}));
    return {...original,summary:{...original.summary,predicted:chosen.summary?.predicted,reference:chosen.summary?.reference,reference_coverage_pct:chosen.summary?.reference_coverage_pct,max_display_Gy:Math.max(original.summary?.max_display_Gy||0,chosen.summary?.max_display_Gy||0)},metrics:rows('metrics'),dvhs:rows('dvhs'),slices:original.slices?{...original.slices,reference:chosen.slices?.reference,isodose_paths:{...original.slices.isodose_paths,reference:chosen.slices?.isodose_paths?.reference}}:original.slices,ring_benchmark:chosen.ring_benchmark,warnings:[...new Set([...(original.warnings||[]),...(chosen.warnings||[])])]};
  }
  function compatible(a,b){
    if(!b.summary?.reference || !b.slices?.reference) return false;
    return ['shape','spacing_mm','indices','z_mm','origin_lps_mm'].every(key=>JSON.stringify(a.slices?.[key])===JSON.stringify(b.slices?.[key]));
  }
  function select(index){
    if(working || !references()[index])return;
    active=index;activating=true;
    try{hooks.activate(mergeReference(base,references()[index].data));}finally{activating=false;}
    render();
  }
  function reset(){epoch++;controller?.abort();controller=null;base=null;extras=[];active=0;snapshot=null;message='';document.getElementById('dose-comparisons')?.remove();document.getElementById('comparison-active-bar')?.remove();}
  function setBase(data){
    if(activating)return;
    reset();base=data;
    const synthetic=Boolean(data.demo || data.summary?.synthetic_demo || ['synthetic','synthetic_demo'].includes(data.source) || ['synthetic','synthetic_demo'].includes(data.summary?.source));
    snapshot=!synthetic && hooks?.canAdd() ? hooks.snapshot() : null;
    if(snapshot)snapshot={...snapshot,targets:[...snapshot.targets],targetPrescriptions:{...snapshot.targetPrescriptions}};
    render();
  }
  async function add(files){
    if(working || !base || !snapshot || !hooks.canAdd())return;
    const pending=Array.from(files||[]),remaining=3-extras.length;
    if(!pending.length)return;
    if(pending.length>remaining){message=`Maximal ${remaining} weitere Originalpläne möglich.`;render();return;}
    const generation=epoch, fixed=snapshot;
    working=true;controller=new AbortController();const signal=controller.signal;
    hooks.busy(true,t('Weitere Originalpläne werden lokal anonymisiert und einzeln analysiert …'),'comparisons');render();
    let failures=0;
    try {
      for(const dose of pending){
        if(generation!==epoch)break;
        let prepared;
        try{
          if(!fixed.structure || fixed.structure.size+dose.size>250*1024*1024-16384)throw new Error('size');
          prepared=await window.DosePrivacy.prepare(fixed.structure,dose);
          if(generation!==epoch || !hooks.canAdd())break;
          if(!prepared.structure || !prepared.dose)throw new Error('prepare');
          const form=new FormData();
          form.append('structure',prepared.structure,'RTSTRUCT.dcm');form.append('dose',prepared.dose,'RTDOSE.dcm');
          form.append('anonymize','true');form.append('targets',JSON.stringify(fixed.targets));form.append('target_prescriptions',JSON.stringify(fixed.targetPrescriptions));form.append('prescription',String(fixed.prescription));form.append('fractions','1');form.append('regularization_sigma_mm',String(fixed.sigma));form.append('calibrated',String(fixed.calibrated===true));
          const data=await hooks.request('/api/predict',{method:'POST',body:form,signal});
          if(generation!==epoch)break;
          if(!compatible(base,data))throw new Error('geometry');
          extras.push({data,label:`Originalplan ${extras.length+2}`});
        }catch(error){if(generation!==epoch || error.name==='AbortError')break;failures++;}
        finally{if(prepared?.context)window.DosePrivacy.forget(prepared.context);}
      }
      if(generation===epoch)message=failures ? `${failures} Originalplan/-pläne konnten nicht sicher verglichen werden. Erfolgreiche Vergleiche bleiben erhalten.` : 'Weitere Originalpläne sind verfügbar.';
    }finally{working=false;controller=null;if(generation===epoch){hooks.busy(false);render();}}
  }
  const svgNode=(tag,attrs,text)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));if(text!==undefined)n.textContent=t(text);return n;};
  const metricTitle=()=>`${t(METRICS[metric][0])} [${METRICS[metric][1]}]`;
  function overview(rows){
    const sources=[{label:'Vorhersage',color:predictionColor(),values:rows.map(r=>r.predicted?.[metric])},...references().filter(r=>r.data?.summary?.reference).map((r,i)=>({label:r.label,color:sourceColor(i),values:rows.map(row=>(r.data.metrics||[]).find(other=>id(row)===id(other))?.reference?.[metric])}))];
    const max=Math.max(1,...sources.flatMap(s=>s.values.filter(finite)))*1.12,width=Math.max(720,rows.length*40+100),height=315;
    const figure=node('figure',undefined,'comparison-overview'),svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-labelledby':'comparison-overview-title comparison-overview-desc'});
    figure.append(node('figcaption','Übersicht aller Targets'));
    const description=sources.map(s=>`${t(s.label)}: ${rows.map((r,i)=>`${r.roi}: ${finite(s.values[i])?s.values[i].toFixed(2):t('nicht verfügbar')}`).join('; ')}`).join('. ');
    svg.append(svgNode('title',{id:'comparison-overview-title'},metricTitle()),svgNode('desc',{id:'comparison-overview-desc'},description));
    for(let i=0;i<=4;i++){const y=240-i*50;svg.append(svgNode('line',{x1:65,x2:width-20,y1:y,y2:y,class:'comparison-grid'}),svgNode('text',{x:55,y:y+4,'text-anchor':'end'},(max*i/4).toLocaleString(window.DoseI18n?.language==='en'?'en-GB':'de-DE',{maximumFractionDigits:2})));}
    svg.append(svgNode('text',{x:65,y:20},metricTitle()),svgNode('text',{x:width/2,y:300,'text-anchor':'middle'},'Targets (kategorial)'));
    rows.forEach((row,i)=>{const x=85+i*(width-120)/Math.max(1,rows.length-1);const label=svgNode('text',{x,y:265,'text-anchor':'middle'},String(i+1));label.append(svgNode('title',{},row.roi));svg.append(label);});
    sources.forEach((source,si)=>source.values.forEach((value,i)=>{if(!finite(value))return;const x=85+i*(width-120)/Math.max(1,rows.length-1)+(si-(sources.length-1)/2)*3,y=240-value/max*200;const point=svgNode('circle',{cx:x,cy:y,r:4.5,fill:source.color,'data-source':si});point.append(svgNode('title',{},`${rows[i].roi} · ${t(source.label)}: ${value.toFixed(2)} ${METRICS[metric][1]}`));svg.append(point);}));
    const scroll=node('div',undefined,'comparison-overview-scroll');scroll.append(svg);figure.append(scroll,node('p','Punkte je Dosisquelle auf gemeinsamer Skala. Targetnummern entsprechen der Reihenfolge der Einzelansichten. Keine Verbindung über fehlende Werte.'));return figure;
  }
  function plot(row,index){
    const sources=[{label:'Vorhersage',value:row.predicted?.[metric],color:predictionColor()},...references().filter(r=>r.data?.summary?.reference).map((r,i)=>({label:r.label,value:(r.data.metrics||[]).find(other=>id(row)===id(other))?.reference?.[metric],color:sourceColor(i)}))];
    const wrap=node('figure',undefined,'comparison-plot'),caption=node('figcaption',`${row.roi || `Target ${index+1}`} · Rx ${row.prescription_Gy ?? '—'} Gy`);wrap.append(caption);
    const valid=sources.map(s=>s.value).filter(finite),max=Math.max(1,...valid)*1.12;
    const svg=svgNode('svg',{viewBox:'0 0 420 250',role:'img','aria-labelledby':`comparison-title-${index} comparison-desc-${index}`});
    svg.append(svgNode('title',{id:`comparison-title-${index}`},`${caption.textContent}: ${t(METRICS[metric][0])}`),svgNode('desc',{id:`comparison-desc-${index}`},sources.map(s=>`${t(s.label)}: ${finite(s.value)?s.value.toFixed(2)+' '+METRICS[metric][1]:t('nicht verfügbar')}`).join('; ')));
    for(let i=0;i<=4;i++){const y=195-i*39;svg.append(svgNode('line',{x1:58,x2:404,y1:y,y2:y,class:'comparison-grid'}),svgNode('text',{x:50,y:y+4,'text-anchor':'end'},(max*i/4).toLocaleString(window.DoseI18n?.language==='en'?'en-GB':'de-DE',{maximumFractionDigits:2})));}
    svg.append(svgNode('text',{x:58,y:18},`${METRICS[metric][0]} [${METRICS[metric][1]}]`),svgNode('text',{x:230,y:244,'text-anchor':'middle'},'Dosisquelle (kategorial)'));
    sources.forEach((s,i)=>{const x=85+i*300/Math.max(1,sources.length-1);svg.append(svgNode('text',{x,y:216,'text-anchor':'middle'},i===0?'Vorh.':s.label.replace('Originalplan','Plan')));if(finite(s.value)){const y=195-s.value/max*156;const circle=svgNode('circle',{cx:x,cy:y,r:6,fill:s.color});circle.append(svgNode('title',{},`${s.label}: ${s.value.toFixed(2)} ${METRICS[metric][1]}`));svg.append(circle,svgNode('text',{x,y:y-11,'text-anchor':'middle'},s.value.toFixed(2)));}else svg.append(svgNode('text',{x,y:185,'text-anchor':'middle'},'—'));});
    wrap.append(svg);return wrap;
  }
  function render(){
    if(!base || !hooks)return;
    document.getElementById('comparison-active-bar')?.remove();
    const bar=node('div',undefined,'comparison-active-bar'),barLabel=node('label','Aktive Referenz in Schnittbildern, DVH und Tabellen (orange)'),barSelect=node('select');bar.id='comparison-active-bar';barSelect.id='comparison-active-source';
    references().forEach((r,i)=>{const option=node('option',r.label+(i===0&&!base.summary?.reference?' (keine Dosis)':''));option.value=i;barSelect.append(option);});barSelect.value=active;barSelect.disabled=working;barSelect.onchange=()=>select(Number(barSelect.value));barLabel.append(barSelect);bar.append(barLabel);document.getElementById('results').prepend(bar);
    let card=document.getElementById('dose-comparisons');if(!card){card=node('section',undefined,'card dose-comparisons');card.id='dose-comparisons';document.getElementById('results').insertBefore(card,document.getElementById('ring-benchmark'));}card.replaceChildren();
    card.append(node('h2','Originalpläne und Kennzahlen je Target'),node('p','Bis zu vier Originalpläne mit identischer Targetauswahl und Verschreibung. Die gemeinsame Ansicht besteht nur in dieser Sitzung. Zusätzliche Analysen werden als getrennte Läufe gespeichert, sofern das Archiv verfügbar ist. DICOM-Downloads gehören weiterhin zur Basisanalyse.'));
    const controls=node('div',undefined,'comparison-controls');
    const sourceLabel=node('label','Originalplan in Schnittbildern, DVH und Tabellen'),source=node('select');source.id='comparison-source';references().forEach((r,i)=>{const opt=node('option',r.label+(i===0&&!base.summary?.reference?' (keine Dosis)':''));opt.value=i;source.append(opt);});source.value=active;source.disabled=working;source.onchange=()=>select(Number(source.value));sourceLabel.append(source);controls.append(sourceLabel);
    const metricLabel=node('label','Kennzahl je Target'),metricSelect=node('select');metricSelect.id='comparison-metric';Object.entries(METRICS).forEach(([key,[label,unit]])=>{const option=node('option',`${label} [${unit}]`);option.value=key;metricSelect.append(option);});metricSelect.value=metric;metricSelect.onchange=()=>{metric=metricSelect.value;render();};metricLabel.append(metricSelect);controls.append(metricLabel);
    if(snapshot && extras.length<3){const label=node('label',`Weitere RTDOSE-Dateien (noch ${3-extras.length})`),input=node('input');input.type='file';input.multiple=true;input.accept='.dcm,application/dicom';input.id='comparison-files';input.disabled=working;label.append(input);const button=node('button','Originalpläne hinzufügen');button.type='button';button.disabled=working;button.onclick=()=>add(input.files);controls.append(label,button);}
    card.append(controls);
    const status=node('p',message);status.setAttribute('role','status');card.append(status);
    if(extras.length)card.append(node('p',`Aktive Referenz: ${references()[active].label}. Vorhersagedosis und DICOM-Exporte bleiben aus der Basisanalyse. Tabellen verwenden die gemeinsame Dosisabdeckung mit der aktiven Referenz; die blauen Plotpunkte stammen aus der Basisanalyse. Lokale Kennzahlen und Ringdaten verlangen vollständige Abdeckung.`));
    const legend=node('div',undefined,'comparison-legend');[{label:'Vorhersage',color:predictionColor()},...references().filter(r=>r.data?.summary?.reference).map((r,i)=>({...r,color:sourceColor(i)}))].forEach(s=>{const item=node('span',s.label),dot=node('i');dot.style.background=s.color;item.prepend(dot);legend.append(item);});card.append(legend,node('p','Punkte zeigen einzelne Dosisquellen; fehlende Werte bleiben leer (—). Die Achse beginnt bei null. Lokales V12 ist geometrisch zugeordnetes Gewebe außerhalb der Targets, ohne Hirnmaske.'));
    const targets=(base.metrics||[]).filter(row=>row.role==='target');card.append(overview(targets));
    const details=node('details'),plots=node('div',undefined,'comparison-plots');details.append(node('summary','Einzelansichten je Target'));targets.forEach((row,index)=>plots.append(plot(row,index)));details.append(plots);card.append(details);
    window.dispatchEvent(new Event('dose-comparisons-change'));
  }
  window.addEventListener('dose-preferences',()=>{if(base)render();});
  window.DoseComparisons={configure:options=>{hooks=options;},getExportComparisons:()=>base?references().map(({label,data})=>({label,data})):[],setBase,reset,refresh:render,add,select,mergeReference};
})();


