/* Dose Atlas, MIT (see ../LICENSE). No external code, requests or persistence.
 * Narrow DICOM PS3.5 Part 10 reader/writer; native Little Endian RT only.
 * DICOM is the registered trademark of NEMA. Not a certified deidentification profile.
 * Anatomy remains identifying information. Original linkage lives in RAM only.
 */
(() => {
  'use strict';
  const DICT = {"00080050":["AccessionNumber","SH"],"00280100":["BitsAllocated","US"],"00280101":["BitsStored","US"],"00280011":["Columns","US"],"30060050":["ContourData","DS"],"30060042":["ContourGeometricType","CS"],"30060016":["ContourImageSequence","SQ"],"30060040":["ContourSequence","SQ"],"00120063":["DeidentificationMethod","LO"],"3004000e":["DoseGridScaling","DS"],"3004000a":["DoseSummationType","CS"],"30040004":["DoseType","CS"],"30040002":["DoseUnits","CS"],"7fe00001":["ExtendedOffsetTable","OV"],"7fe00002":["ExtendedOffsetTableLengths","OV"],"00200052":["FrameOfReferenceUID","UI"],"3004000c":["GridFrameOffsetVector","DS"],"00280102":["HighBit","US"],"00200037":["ImageOrientationPatient","DS"],"00200032":["ImagePositionPatient","DS"],"00080060":["Modality","CS"],"30060046":["NumberOfContourPoints","IS"],"00280008":["NumberOfFrames","IS"],"00100030":["PatientBirthDate","DA"],"00100020":["PatientID","LO"],"00120062":["PatientIdentityRemoved","CS"],"00100010":["PatientName","PN"],"00100040":["PatientSex","CS"],"00280004":["PhotometricInterpretation","CS"],"7fe00010":["PixelData","OB"],"00280103":["PixelRepresentation","US"],"00280030":["PixelSpacing","DS"],"30060039":["ROIContourSequence","SQ"],"30060026":["ROIName","LO"],"30060022":["ROINumber","IS"],"30060014":["RTReferencedSeriesSequence","SQ"],"30060012":["RTReferencedStudySequence","SQ"],"30060010":["ReferencedFrameOfReferenceSequence","SQ"],"30060024":["ReferencedFrameOfReferenceUID","UI"],"30060084":["ReferencedROINumber","IS"],"300c0002":["ReferencedRTPlanSequence","SQ"],"00081150":["ReferencedSOPClassUID","UI"],"00081155":["ReferencedSOPInstanceUID","UI"],"300c0060":["ReferencedStructureSetSequence","SQ"],"00280010":["Rows","US"],"00080016":["SOPClassUID","UI"],"00080018":["SOPInstanceUID","UI"],"00280002":["SamplesPerPixel","US"],"0020000e":["SeriesInstanceUID","UI"],"00080005":["SpecificCharacterSet","CS"],"30060020":["StructureSetROISequence","SQ"],"00080020":["StudyDate","DA"],"00200010":["StudyID","SH"],"0020000d":["StudyInstanceUID","UI"],"00080030":["StudyTime","TM"],"00020010":["TransferSyntaxUID","UI"]};
  const OMIT = new Set('PatientName PatientID PatientBirthDate PatientSex SpecificCharacterSet StudyDate StudyTime AccessionNumber StudyID PatientIdentityRemoved DeidentificationMethod TransferSyntaxUID ExtendedOffsetTable ExtendedOffsetTableLengths'.split(' '));
  const RESTORE = new Set('PatientName PatientID PatientBirthDate PatientSex SpecificCharacterSet StudyDate StudyTime AccessionNumber StudyID StudyInstanceUID'.split(' '));
  // PS3.3 Source Pixel Planes Characteristics: retain the original contour
  // sampling orientation/spacing, including oblique HDSS structure exports.
  DICT['3006004a']=['SourcePixelPlanesCharacteristicsSequence','SQ'];
  DICT['00180088']=['SpacingBetweenSlices','DS'];
  DICT['00180050']=['SliceThickness','DS'];
  const LONG = new Set('OB OD OF OL OV OW SQ UC UR UT UN SV UV'.split(' '));
  const contexts = new WeakMap(), encoder = new TextEncoder(), decoder = new TextDecoder('latin1');
  const fail = (code) => { throw new Error(`DICOM-Datenschutz: ${code}. Nicht übertragen; nur unkomprimiertes DICOM Part 10 Little Endian RTSTRUCT/RTDOSE wird unterstützt.`); };
  const value = e => e ? decoder.decode(e.data).replace(/[\0 ]+$/g, '') : '';
  const find = (ds, name) => ds.find(e => DICT[e.tag]?.[0] === name);
  const text = (tag, vr, s) => { let b = encoder.encode(s); if(b.length % 2) { const c = new Uint8Array(b.length+1); c.set(b); c[b.length]=vr==='UI'?0:32; b=c; } return {tag,vr,data:b}; };
  function parse(buffer) {
    const b=new Uint8Array(buffer), view=new DataView(b.buffer,b.byteOffset,b.byteLength); let p=132, count=0;
    if(b.length<132 || decoder.decode(b.subarray(128,132))!=='DICM') fail('PART10_REQUIRED');
    const need=n=>{if(p+n>b.length)fail('TRUNCATED');};
    const u16=()=>{need(2);const n=view.getUint16(p,true);p+=2;return n;};
    const u32=()=>{need(4);const n=view.getUint32(p,true);p+=4;return n;};
    const tag=()=>u16().toString(16).padStart(4,'0')+u16().toString(16).padStart(4,'0');
    function dataset(end, explicit, depth=0, delimiter=null) {
      if(depth>32)fail('SEQUENCE_DEPTH'); const ds=[],seen=new Set();
      while(p<end) {
        if(++count>2000000)fail('ELEMENT_LIMIT');
        const t=tag();
        if(t==='fffee00d'||t==='fffee0dd') {if(u32()!==0||t!==delimiter)fail('DELIMITER');return ds;}
        if(t.startsWith('fffe')||seen.has(t))fail('DUPLICATE_OR_ITEM');seen.add(t);
        let vr,n;
        if(explicit){need(2);vr=decoder.decode(b.subarray(p,p+2));p+=2;if(!/^[A-Z]{2}$/.test(vr))fail('VR');if(LONG.has(vr)){if(u16()!==0)fail('RESERVED');n=u32();}else n=u16();}
        else {vr=DICT[t]?.[1]||'UN';n=u32();}
        if(n!==0xffffffff&&(n%2||p+n>end))fail('LENGTH');
        const e={tag:t,vr};
        if(vr==='SQ'||n===0xffffffff) {
          if(vr!=='SQ'&&(explicit||t==='7fe00010'))fail('UNSUPPORTED_UNDEFINED_VALUE');
          e.vr='SQ';e.items=[];const stop=n===0xffffffff?end:p+n;let closed=false;
          while(p<stop){const it=tag(),len=u32();if(it==='fffee0dd'){if(n!==0xffffffff||len!==0)fail('SEQUENCE_END');closed=true;break;}if(it!=='fffee000')fail('ITEM');const until=len===0xffffffff?stop:p+len;if(until>stop)fail('ITEM_LENGTH');e.items.push(dataset(until,explicit,depth+1,len===0xffffffff?'fffee00d':null));if(len!==0xffffffff&&p!==until)fail('ITEM_END');}
          if(n===0xffffffff&&!closed)fail('MISSING_SEQUENCE_END');if(n!==0xffffffff&&p!==stop)fail('SEQUENCE_LENGTH');
        } else {need(n);e.data=b.subarray(p,p+n);p+=n;}
        ds.push(e);
      }
      if(delimiter)fail('MISSING_ITEM_END');return ds;
    }
    const meta=[];
    while(p+8<=b.length&&view.getUint16(p,true)===2){const t=tag();need(2);const vr=decoder.decode(b.subarray(p,p+2));p+=2;let n;if(LONG.has(vr)){u16();n=u32();}else n=u16();need(n);meta.push({tag:t,vr,data:b.subarray(p,p+n)});p+=n;}
    const ts=value(meta.find(e=>e.tag==='00020010'));
    if(!['1.2.840.10008.1.2','1.2.840.10008.1.2.1'].includes(ts))fail('TRANSFER_SYNTAX_UNSUPPORTED');
    return dataset(b.length,ts.endsWith('.1'));
  }
  function concat(parts){const out=new Uint8Array(parts.reduce((n,b)=>n+b.length,0));let p=0;for(const b of parts){out.set(b,p);p+=b.length;}return out;}
  function head(tag,vr,length,item=false){const b=new Uint8Array(item?8:LONG.has(vr)?12:8),v=new DataView(b.buffer);v.setUint16(0,parseInt(tag.slice(0,4),16),true);v.setUint16(2,parseInt(tag.slice(4),16),true);if(item)v.setUint32(4,length,true);else{b[4]=vr.charCodeAt(0);b[5]=vr.charCodeAt(1);if(LONG.has(vr))v.setUint32(8,length,true);else{if(length>65534)fail('VALUE_TOO_LONG');v.setUint16(6,length,true);}}return b;}
  function encode(ds){return concat([...ds].sort((a,b)=>a.tag.localeCompare(b.tag)).map(e=>{const data=e.items?concat(e.items.map(ds=>{const b=encode(ds);return concat([head('fffee000','',b.length,true),b]);})):e.data;return concat([head(e.tag,e.vr,data.length),data]);}));}
  function write(ds){const pre=new Uint8Array(132);pre.set(encoder.encode('DICM'),128);const meta=encode([{tag:'00020001',vr:'OB',data:new Uint8Array([0,1])},text('00020002','UI',value(find(ds,'SOPClassUID'))),text('00020003','UI',value(find(ds,'SOPInstanceUID'))),text('00020010','UI','1.2.840.10008.1.2.1'),text('00020012','UI','2.25.31885325162103420973445306206200995274'),text('00020013','SH','DOSEATLAS_1')]);const len=new Uint8Array(4);new DataView(len.buffer).setUint32(0,meta.length,true);return new Blob([pre,head('00020000','UL',4),len,meta,encode(ds)],{type:'application/dicom'});}
  function uid(){const bytes=crypto.getRandomValues(new Uint8Array(16));return '2.25.'+bytes.reduce((a,x)=>(a<<8n)|BigInt(x),0n).toString();}
  const validUid=s=>/^\d+(\.\d+)*$/.test(s)&&s.length<=64;
  // Reject malformed retained values instead of carrying hidden free text across.
  function retainedChecks(ds, allowPixels=false) {for(const e of ds){const def=DICT[e.tag];if(!def||OMIT.has(def[0]))continue;
    if(e.vr!==def[1]&&!(def[0]==='PixelData'&&e.vr==='OW'))fail('RETAINED_VR_MISMATCH');
    if(e.items){e.items.forEach(item=>retainedChecks(item,false));continue;}
    if(def[0]==='PixelData'){if(!allowPixels)fail('UNEXPECTED_PIXEL_DATA');continue;}
    const s=value(e);
    if(e.vr==='UI'&&!s.split('\\').every(validUid))fail('INVALID_UID');
    if(['DS','IS'].includes(e.vr)&&!s.split('\\').every(x=>x.trim()!==''&&/^[ +\-0-9.eE]+$/.test(x)&&Number.isFinite(Number(x))&&(e.vr!=='IS'||Number.isInteger(Number(x)))))fail('NUMERIC_VALUE');
    if(e.vr==='US'&&(e.data.length!==2))fail('NUMERIC_LENGTH');
    const enums={Modality:['RTSTRUCT','RTDOSE'],ContourGeometricType:['POINT','OPEN_PLANAR','OPEN_NONPLANAR','CLOSED_PLANAR','CLOSEDPLANAR_XOR'],PhotometricInterpretation:['MONOCHROME2'],DoseUnits:['GY','RELATIVE'],DoseType:['PHYSICAL','EFFECTIVE','ERROR'],DoseSummationType:['PLAN','MULTI_PLAN','FRACTION','BEAM','BRACHY','CONTROL_POINT','RECORD','SESSION','BEAM_SESSION','BRACHY_SESSION','FRACTION_SESSION','OTHER']};
    if(e.vr==='CS'&&!enums[def[0]]?.includes(s))fail('CODE_VALUE');
  }}
  function dosePixels(ds){const number=name=>{const e=find(ds,name);return e?.vr==='US'&&e.data.length===2?new DataView(e.data.buffer,e.data.byteOffset,2).getUint16(0,true):NaN;};
    const rows=number('Rows'),cols=number('Columns'),bits=number('BitsAllocated'),frames=Number(value(find(ds,'NumberOfFrames')));
    if(![16,32].includes(bits)||number('BitsStored')!==bits||number('HighBit')!==bits-1||number('PixelRepresentation')!==0||number('SamplesPerPixel')!==1||!Number.isInteger(frames)||frames<1||rows<1||cols<1)fail('PIXEL_LAYOUT');
    const expected=rows*cols*frames*bits/8,pixels=find(ds,'PixelData');if(!Number.isSafeInteger(expected)||expected>256*1024*1024||pixels?.data?.length!==expected)fail('PIXEL_LENGTH');
  }
  function clean(ds,map){return ds.filter(e=>DICT[e.tag]&&!OMIT.has(DICT[e.tag][0])).map(e=>{const name=DICT[e.tag][0];if(e.items)return {...e,items:e.items.map(x=>clean(x,map))};if(e.vr==='UI'&&!['SOPClassUID','ReferencedSOPClassUID'].includes(name)){return text(e.tag,e.vr,value(e).split('\\').map(s=>{if(!validUid(s))fail('INVALID_UID');if(!map.has(s))map.set(s,uid());return map.get(s);}).join('\\'));}if(name==='ROIName'){const n=value(find(ds,'ROINumber'));if(!/^\d+$/.test(n))fail('ROI_NUMBER');const s=value(e).toLowerCase().trim();const kind=/^ptv/.test(s)?'PTV':/^gtv/.test(s)?'GTV':/brainstem|hirnstamm/.test(s)?'Brainstem':/chiasm/.test(s)?'Chiasm':/optic|sehnerv/.test(s)?'Optic':/^(brain|hirn|gehirn)$/.test(s)?'Brain':'ROI';return text(e.tag,'LO',kind+' '+n);}return e;});}
  function validate(ds,modality){if(value(find(ds,'Modality'))!==modality||value(find(ds,'SOPClassUID'))!==(modality==='RTSTRUCT'?'1.2.840.10008.5.1.4.1.1.481.3':'1.2.840.10008.5.1.4.1.1.481.2'))fail('RT_MODALITY');for(const name of ['StudyInstanceUID','SOPInstanceUID','SeriesInstanceUID'])if(!validUid(value(find(ds,name))))fail('REQUIRED_UID');}
  async function prepare(structureFile,doseFile=null){
    if(!structureFile||structureFile.size>256*1024*1024||doseFile?.size>256*1024*1024)fail('FILE_SIZE');
    const structure=parse(await structureFile.arrayBuffer()),dose=doseFile?parse(await doseFile.arrayBuffer()):null;validate(structure,'RTSTRUCT');if(dose)validate(dose,'RTDOSE');
    const frames=new Set();const visit=ds=>{for(const e of ds){if(['FrameOfReferenceUID','ReferencedFrameOfReferenceUID'].includes(DICT[e.tag]?.[0]))frames.add(value(e));if(e.items)e.items.forEach(visit);}};visit(structure);
    if(frames.size!==1||![...frames].every(validUid))fail('SINGLE_FRAME_REQUIRED');const frame=[...frames][0];
    if(dose&&value(find(dose,'FrameOfReferenceUID'))!==frame)fail('FRAME_MISMATCH');
    retainedChecks(structure);if(dose){retainedChecks(dose,true);dosePixels(dose);}
    const metadata=structure.filter(e=>RESTORE.has(DICT[e.tag]?.[0])).map(e=>({...e,data:e.data.slice()}));
    const context=Object.freeze({});contexts.set(context,{metadata,frame,structureSOP:value(find(structure,'SOPInstanceUID'))});
    const map=new Map(),sanitize=ds=>{const c=clean(ds,map);c.push(text('00100010','PN','ANON'),text('00100020','LO','ANON'),text('00120062','CS','YES'),text('00120063','LO','Dose Atlas browser minimal allowlist; anatomy retained'));return write(c);};
    return {structure:new File([sanitize(structure)],'RTSTRUCT.dcm',{type:'application/dicom'}),dose:dose?new File([sanitize(dose)],'RTDOSE.dcm',{type:'application/dicom'}):null,context};
  }
  async function restoreOriginalCase(blob,context){const original=contexts.get(context);if(!original)fail('ORIGINAL_CONTEXT_EXPIRED');const ds=parse(await blob.arrayBuffer());validate(ds,'RTDOSE');
    const out=ds.filter(e=>!RESTORE.has(DICT[e.tag]?.[0])&&!['00200052','00120062','00120063','300c0002','300c0060'].includes(e.tag));
    const rebind=items=>items.map(e=>e.items?{...e,items:e.items.map(rebind)}:['00200052','30060024'].includes(e.tag)?text(e.tag,'UI',original.frame):e);
    out.push(...original.metadata,text('00200052','UI',original.frame),text('00120062','CS','NO'),{tag:'300c0060',vr:'SQ',items:[[text('00081150','UI','1.2.840.10008.5.1.4.1.1.481.3'),text('00081155','UI',original.structureSOP)]]});return write(rebind(out));
  }
  function download(blob,filename='RTDOSE_original_case.dcm'){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=/^[A-Za-z0-9_.-]+$/.test(filename)?filename:'RTDOSE.dcm';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
  let publicPrepare=prepare,publicRestore=restoreOriginalCase,publicForget=context=>contexts.delete(context);
  // The same self-hosted script is the worker entry point. Originals and their
  // linkage stay in this dedicated worker's RAM; only sanitized Files return.
  if(typeof WorkerGlobalScope!=='undefined'&&globalThis instanceof WorkerGlobalScope){
    let originalContext=null;
    globalThis.onmessage=async({data})=>{const {id,operation}=data;try{
      if(operation==='prepare'){const result=await prepare(data.structure,data.dose);originalContext=result.context;globalThis.postMessage({id,result:{structure:result.structure,dose:result.dose}});}
      else if(operation==='restore'){globalThis.postMessage({id,result:await restoreOriginalCase(data.blob,originalContext)});}
      else fail('WORKER_OPERATION');
    }catch(error){globalThis.postMessage({id,error:error instanceof Error&&error.message.startsWith('DICOM-Datenschutz:')?error.message:'DICOM-Datenschutz: WORKER_FAILED. Nicht übertragen.'});}};
  }else if(typeof document!=='undefined'&&typeof Worker!=='undefined'&&document.currentScript?.src){
    const workerURL=document.currentScript.src,workers=new WeakMap();
    publicPrepare=async(structure,dose=null)=>{
      const worker=new Worker(workerURL),pending=new Map();let next=0,closed=false;
      const close=()=>{closed=true;worker.terminate();for(const {reject} of pending.values())reject(new Error('DICOM-Datenschutz: WORKER_CLOSED. Nicht übertragen.'));pending.clear();};
      worker.onmessage=({data})=>{const item=pending.get(data.id);if(!item)return;pending.delete(data.id);data.error?item.reject(new Error(data.error)):item.resolve(data.result);};
      worker.onerror=()=>close();worker.onmessageerror=()=>close();
      const call=(operation,payload)=>new Promise((resolve,reject)=>{if(closed){reject(new Error('DICOM-Datenschutz: ORIGINAL_CONTEXT_EXPIRED.'));return;}const id=++next;pending.set(id,{resolve,reject});try{worker.postMessage({id,operation,...payload});}catch{pending.delete(id);reject(new Error('DICOM-Datenschutz: WORKER_MESSAGE_FAILED.'));}});
      try{const result=await call('prepare',{structure,dose});const context=Object.freeze({});workers.set(context,{call,close});return {...result,context};}catch(error){close();throw error;}
    };
    publicRestore=(blob,context)=>{const state=workers.get(context);if(!state)fail('ORIGINAL_CONTEXT_EXPIRED');return state.call('restore',{blob});};
    publicForget=context=>{const state=workers.get(context);if(!state)return false;state.close();workers.delete(context);return true;};
  }
  globalThis.DosePrivacy=Object.freeze({prepare:publicPrepare,restoreOriginalCase:publicRestore,download,forget:publicForget,anonymizePair:async(s,d)=>{const r=await publicPrepare(s,d);return {structureFile:r.structure,doseFile:r.dose,context:r.context};}});
})();
