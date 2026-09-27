/* Local OOXML workbook export. No network, dependencies, formulas or source-file metadata. */
(() => {
  'use strict';
  const MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  const NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
  const encoder = new TextEncoder();
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const safeKey = key => !/(?:uid|filename|file_name|patient|identifier|(?:^|_)id(?:_|$))/i.test(key);
  const keys = objects => [...new Set(objects.flatMap(object => Object.entries(object || {}).filter(([key,value]) => safeKey(key) && (value === null || finite(value))).map(([key]) => key)))].sort();
  const numeric = value => finite(value) ? value : null;
  function xml(value) {
    // OOXML escape literals first; then represent control characters using Excel escapes.
    return String(value).replace(/[\ud800-\udfff]/gu, '\ufffd').replace(/_x[0-9a-f]{4}_/gi, s => '_x005F_' + s.slice(1))
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g, c => '_x' + c.charCodeAt(0).toString(16).padStart(4,'0') + '_')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&apos;');
  }
  function column(index) { let text=''; for (++index; index; index=Math.floor((index-1)/26)) text=String.fromCharCode(65+(index-1)%26)+text; return text; }
  function sheet(rows) {
    const last=column(rows[0].length-1)+rows.length;
    const cells=rows.map((row,i) => `<row r="${i+1}">${row.map((value,j) => {
      if (value === null || value === undefined || (typeof value==='number' && !finite(value))) return '';
      const ref=column(j)+(i+1), style=i===0?1:finite(value)?2:0;
      return finite(value) ? `<c r="${ref}" s="${style}"><v>${value}</v></c>` : `<c r="${ref}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${xml(value)}</t></is></c>`;
    }).join('')}</row>`).join('');
    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="${NS}"><dimension ref="A1:${last}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultColWidth="22" defaultRowHeight="15"/><sheetData>${cells}</sheetData><autoFilter ref="A1:${last}"/></worksheet>`;
  }
  const crcTable=Uint32Array.from({length:256},(_,n)=>{for(let i=0;i<8;i++) n=(n&1)?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
  function crc(bytes) { let n=0xffffffff; for(const b of bytes)n=crcTable[(n^b)&255]^(n>>>8);return (n^0xffffffff)>>>0; }
  function zip(files) {
    const chunks=[],central=[];let offset=0,centralSize=0;
    const header=(size,fields)=>{const b=new Uint8Array(size),v=new DataView(b.buffer);for(const [at,value,width] of fields)width===4?v.setUint32(at,value,true):v.setUint16(at,value,true);return b;};
    for(const [name,text] of files){
      const filename=encoder.encode(name),data=encoder.encode(text),checksum=crc(data);
      const local=header(30,[[0,0x04034b50,4],[4,20,2],[6,0x800,2],[12,33,2],[14,checksum,4],[18,data.length,4],[22,data.length,4],[26,filename.length,2]]);
      const directory=header(46,[[0,0x02014b50,4],[4,20,2],[6,20,2],[8,0x800,2],[14,33,2],[16,checksum,4],[20,data.length,4],[24,data.length,4],[28,filename.length,2],[42,offset,4]]);
      chunks.push(local,filename,data);central.push(directory,filename);offset+=local.length+filename.length+data.length;centralSize+=directory.length+filename.length;
    }
    const end=header(22,[[0,0x06054b50,4],[8,files.length,2],[10,files.length,2],[12,centralSize,4],[16,offset,4]]);
    const all=[...chunks,...central,end],bytes=new Uint8Array(all.reduce((n,b)=>n+b.length,0));let cursor=0;for(const b of all){bytes.set(b,cursor);cursor+=b.length;}return bytes;
  }
  function tables(comparisons) {
    if(!Array.isArray(comparisons)||!comparisons.length)throw new Error('Keine Metriken zum Exportieren vorhanden.');
    const sources=['predicted','reference'], data=comparisons.map(c=>c.data || {});
    const metricKeys=keys(data.flatMap(d=>(d.metrics||[]).flatMap(m=>sources.map(s=>m[s]))));
    const summaryKeys=keys(data.flatMap(d=>sources.map(s=>d.summary?.[s])));
    const contextKeys=keys(data.map(d=>d.summary));
    const metrics=[['comparison','source','ROI_name','ROI_number','role','prescription_Gy','volume_cc','local_coverage_pct','local_region_cc',...metricKeys]];
    const summary=[['comparison','source',...contextKeys.map(k=>'context_'+k),...summaryKeys]];
    const metadata=[['comparison','field','value'],['Alle','evaluation','Nur Forschungs-/Evaluationszwecke; keine klinisch validierte Therapieplanung.'],
      ['Alle','domain','Auswertung nur in der 35-mm-Umgebung der Targets; keine Ganzhirn- oder Gesamtplanmetriken.'],
      ['Alle','local_V12_cc','Geometrisch zugeordnetes Gewebe außerhalb der Targets; keine Hirnmaske, kein Brain-minus-PTV-V12.'],
      ['Alle','missing_values','Fehlende, nicht endliche oder nicht auswertbare Werte bleiben leer; numerische Null bleibt 0.'],
      ['Alle','means','Lokale Mittelwerte verwenden bei Referenz vorhandene, beidseitig auswertbare Targets; zugehörige *_n enthalten die Anzahl.'],
      ['Alle','inverse_CI','Mittelwert von 1/CI ist nicht der Kehrwert des mittleren CI.'],
      ['Alle','mixed_prescriptions','Bei unterschiedlichen Target-Verschreibungen bleiben nicht definierte globale CI/GI-Werte leer. Lokale Metriken verwenden die jeweilige Target-Verschreibung.'],
      ['Alle','matched_coverage','Vorhersagemetriken werden je Vergleich aus dessen vollständiger Antwort exportiert. Trotz identischem Vorhersagedosisfeld können sie sich durch die gemeinsame Referenzabdeckung unterscheiden.'],
      ['Alle','units','Spaltennamen: _Gy = Gy, _cc = cm³, _pct = %, _mm = mm; CI und GI dimensionslos, *_n = Anzahl.'],
      ['Alle','privacy','Keine ursprünglichen Dateinamen, Patientenkennungen oder DICOM-UID-Felder. ROI-Namen werden wie angezeigt übernommen.']];
    comparisons.forEach((comparison,index)=>{
      const d=data[index],s=d.summary||{},label=comparison.label || `Originalplan ${index+1}`;
      for(const source of sources){
        if(source==='reference' && !s.reference && !(d.metrics||[]).some(m=>m.reference))continue;
        for(const row of d.metrics||[])metrics.push([label,source==='predicted'?'Prediction':'Reference',row.roi??row.name??'',numeric(row.number??row.roi_number),row.role??'',numeric(row.prescription_Gy),numeric(row.volume_cc),numeric(row.local_coverage_pct),numeric(row.local_region_cc),...metricKeys.map(k=>numeric(row[source]?.[k]))]);
        summary.push([label,source==='predicted'?'Prediction':'Reference',...contextKeys.map(k=>numeric(s[k])),...summaryKeys.map(k=>numeric(s[source]?.[k]))]);
      }
      const demo=Boolean(d.demo||s.synthetic_demo||['synthetic','synthetic_demo'].includes(d.source)||['synthetic','synthetic_demo'].includes(s.source));
      metadata.push([label,'synthetic_demo',demo?'Ja – synthetische Demonstration':'Nein / nicht als synthetisch gekennzeichnet'],
        [label,'model_variant',s.prediction_calibration_scale>1?`Empirisch kalibriert v${s.prediction_calibration_version}; Faktor ${s.prediction_calibration_scale}; Sigma ${s.prediction_regularization_sigma_mm} mm`:s.prediction_regularization_sigma_mm===0?'Rohvorhersage':finite(s.prediction_regularization_sigma_mm)?`Regularisiert; Sigma ${s.prediction_regularization_sigma_mm} mm`:'Nicht angegeben'],
        [label,'prescription_min_Gy',numeric(s.prescription_range_Gy?.[0]??s.prescription_Gy)],
        [label,'prescription_max_Gy',numeric(s.prescription_range_Gy?.[1]??s.prescription_Gy)],
        [label,'fractions',numeric(s.fractions)], [label,'reference_coverage_pct',numeric(s.reference_coverage_pct)]);
    });
    return [['ROI-Metriken',metrics],['Zusammenfassung',summary],['Methodik',metadata]];
  }
  async function build(comparisons) {
    const sheets=tables(comparisons),files=[];
    files.push(['[Content_Types].xml',`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${sheets.map((_,i)=>`<Override PartName="/xl/worksheets/sheet${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`]);
    const relationshipNS='http://schemas.openxmlformats.org/package/2006/relationships',officeNS='http://schemas.openxmlformats.org/officeDocument/2006/relationships';
    files.push(['_rels/.rels',`<Relationships xmlns="${relationshipNS}"><Relationship Id="rId1" Type="${officeNS}/officeDocument" Target="xl/workbook.xml"/></Relationships>`]);
    files.push(['xl/workbook.xml',`<workbook xmlns="${NS}" xmlns:r="${officeNS}"><sheets>${sheets.map(([name],i)=>`<sheet name="${xml(name)}" sheetId="${i+1}" r:id="rId${i+1}"/>`).join('')}</sheets></workbook>`]);
    files.push(['xl/_rels/workbook.xml.rels',`<Relationships xmlns="${relationshipNS}">${sheets.map((_,i)=>`<Relationship Id="rId${i+1}" Type="${officeNS}/worksheet" Target="worksheets/sheet${i+1}.xml"/>`).join('')}<Relationship Id="rId4" Type="${officeNS}/styles" Target="styles.xml"/></Relationships>`]);
    files.push(['xl/styles.xml',`<styleSheet xmlns="${NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`]);
    sheets.forEach(([,rows],i)=>files.push([`xl/worksheets/sheet${i+1}.xml`,sheet(rows)]));
    return new Blob([zip(files)],{type:MIME});
  }
  async function download(comparisons) {
    const blob=await build(comparisons),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download='Dose-Atlas-Metriken.xlsx';document.body.append(a);
    try { a.click(); } finally { a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000); }
  }
  window.DoseMetricsExport=Object.freeze({build,download});
})();
