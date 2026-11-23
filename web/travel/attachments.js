'use strict';
window.Attachments=(()=>{
 const files=new Map(),label=kind=>kind==='invoice'?'发票':'凭证';let db,working=false,invoiceBusy=false;
 const el=id=>document.getElementById(id),notice=text=>{el('attachment-status').textContent=text;};
 function transaction(mode,action){return new Promise((resolve,reject)=>{const tx=db.transaction('files',mode),request=action(tx.objectStore('files'));tx.oncomplete=()=>resolve(request?.result);tx.onabort=tx.onerror=()=>reject(tx.error||new Error('无法读写本机附件存储'));});}
 function list(){return [...files.values()].sort((a,b)=>(a.kind==='invoice'?0:1)-(b.kind==='invoice'?0:1)||a.order-b.order);}
 function render(){const records=list(),invoiceCount=records.filter(x=>x.kind==='invoice').length;el('attachment-count').textContent=`发票 ${invoiceCount} 个 · 凭证 ${records.length-invoiceCount} 个`;el('attachment-list').replaceChildren();el('attachment-empty').hidden=records.length>0;for(const record of records){const li=document.createElement('li'),tag=document.createElement('span'),name=document.createElement('span'),size=document.createElement('small'),download=document.createElement('button'),remove=document.createElement('button');tag.className='file-kind';tag.textContent=label(record.kind);name.className='file-name';name.textContent=record.name;name.title=record.name;size.textContent=(record.blob.size/1024/1024).toFixed(2)+' MB';download.type=remove.type='button';download.textContent='下载';download.className='file-download';download.setAttribute('aria-label','下载'+record.name);download.onclick=()=>saveBlob(record.blob,record.name);remove.className='remove';remove.textContent='×';remove.setAttribute('aria-label',(record.kind==='invoice'?'删除发票及关联费用：':'删除凭证：')+record.name);remove.onclick=()=>{if(!working&&!invoiceBusy)removeFile(record.id).catch(error=>notice(error.message));};li.append(tag,name,size,download,remove);el('attachment-list').append(li);}for(const id of ['download-originals','download-print-pdf','print-attachments'])el(id).disabled=working||invoiceBusy||records.length===0;el("reidentify-invoices").disabled=working||invoiceBusy||invoiceCount===0;}
 const ready=new Promise((resolve,reject)=>{const request=indexedDB.open('travel-reimbursement-attachments-v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('files',{keyPath:'id'});request.onerror=()=>reject(request.error);request.onsuccess=()=>{db=request.result;db.onversionchange=()=>db.close();resolve();};}).then(async()=>{for(const record of await transaction('readonly',store=>store.getAll()))files.set(record.id,record);render();}).catch(()=>{db=null;notice('本机附件存储不可用。附件仅保留在本次页面，请在关闭前下载。');render();});
 async function addFile(file,kind,hash){await ready;if(!['invoice','proof'].includes(kind))throw new Error('未知附件类别');if(!/\.(pdf|jpe?g|png|webp)$/i.test(file.name)||file.size===0||file.size>20*1024*1024)throw new Error('请使用 20MB 以内的 PDF、JPG、PNG 或 WebP');hash=hash||sha256(await file.arrayBuffer());const id=kind+':'+hash;if(files.has(id))return false;if([...files.values()].reduce((sum,x)=>sum+x.blob.size,0)+file.size>200*1024*1024)throw new Error('本机附件合计最多 200MB，请先下载并清理已完成的附件');const record={id,hash,kind,name:file.name,blob:file,order:Math.max(0,...[...files.values()].map(x=>x.order))+1};if(db)try{await transaction('readwrite',store=>store.put(record));}catch{throw new Error('本机附件保存失败，请检查浏览器存储空间后重试');}files.set(id,record);render();return true;}
 async function removeFile(id){await ready;const record=files.get(id);if(!record)return;if(db)await transaction('readwrite',store=>store.delete(id));files.delete(id);render();document.dispatchEvent(new CustomEvent('attachmentremoved',{detail:{hash:record.hash,kind:record.kind}}));}
 async function clear(){await ready;if(working||invoiceBusy)throw new Error('附件正在处理，请稍后清除');if(db)await transaction('readwrite',store=>store.clear());files.clear();render();notice('本机附件已清除。');}
 function saveBlob(blob,name,message='文件已生成。',target=el('attachment-status')){
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name.replace(/[\\/:*?"<>|\x00-\x1f]/g,'_');a.className='download-result';a.textContent='再次保存：'+a.download;
  target.textContent=message+' 浏览器通常保存到“下载”文件夹；没有看到文件时，请点击下方链接。';target.append(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(url);if(a.isConnected){a.removeAttribute('href');a.textContent='下载链接已过期，请重新点击下载按钮。';}},600000);
 }
 function filename(suffix){const names=document.getElementById('names').value.trim(),place=document.getElementById('filePlace').value.trim(),date=document.getElementById('start').value.replace(/-/g,'');return ([names,place,date].filter(Boolean).join('-')||'差旅报销')+'-'+suffix;}
 async function previewAndPrint(bytes,preview,name,pageCount){
  const nav=document.createElement('nav'),button=document.createElement('button'),download=document.createElement('button'),close=document.createElement('button'),status=document.createElement('span');
  button.id='print-all';button.textContent='打印全部';button.disabled=true;button.type=download.type=close.type='button';button.onclick=()=>window.print();
  download.textContent='下载此 PDF';download.onclick=()=>saveBlob(new Blob([bytes],{type:'application/pdf'}),name,'打印 PDF 已生成。',status);
  close.textContent='关闭预览';close.onclick=()=>preview.close();status.textContent=`正在准备 ${pageCount} 页…`;nav.append(button,download,close,status);preview.replaceChildren(nav);
  const lib=await import('./vendor/pdf.min.mjs');lib.GlobalWorkerOptions.workerSrc=new URL('vendor/pdf.worker.min.mjs',location.href).href;
  const pdf=await lib.getDocument({data:bytes.slice(),isEvalSupported:false,useSystemFonts:true,cMapUrl:new URL('vendor/cmaps/',location.href).href,cMapPacked:true,standardFontDataUrl:new URL('vendor/standard_fonts/',location.href).href,wasmUrl:new URL('vendor/wasm/',location.href).href}).promise;
  try{
   for(let i=1;i<=pdf.numPages;i++){
    if(!preview.isConnected)throw new Error('打印预览已关闭');
    const page=await pdf.getPage(i),base=page.getViewport({scale:1}),viewport=page.getViewport({scale:Math.min(2.5,3000/Math.max(base.width,base.height))}),canvas=document.createElement('canvas'),article=document.createElement('article');
    canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);canvas.setAttribute('aria-label',`第 ${i} 页`);
    await page.render({canvasContext:canvas.getContext('2d'),viewport}).promise;article.className='page';article.append(canvas);preview.append(article);page.cleanup();status.textContent=`已准备 ${i}/${pdf.numPages} 页`;
   }
   status.textContent=`共 ${pdf.numPages} 页 · 发票每页一张，凭证每页两张`;button.disabled=false;
  }finally{await pdf.destroy();}
 }
 async function run(action,win){
  await ready;if(working||invoiceBusy){win?.close();notice('附件仍在处理，请稍候再下载或打印');return;}
  working=true;notice(action==='zip'?'正在生成原始附件 ZIP…':'正在生成打印 PDF…');render();
  try{
   const records=list();if(!records.length)throw new Error('请先添加发票或凭证');
   if(action==='zip'){
    const zip=new JSZip();records.forEach((record,i)=>zip.file(`${label(record.kind)}/${String(i+1).padStart(3,'0')}_${record.name.replace(/[\\/:*?"<>|\x00-\x1f]/g,'_')}`,record.blob));
    saveBlob(await zip.generateAsync({type:'blob',compression:'STORE'}),filename('原始附件.zip'),`已生成 ${records.length} 个原始附件的 ZIP。`);
   }else{
    const result=await PrintBundle.buildPdf(records,notice),name=filename('发票及凭证打印.pdf');
    if(action==='print'){await previewAndPrint(result.bytes,win,name,result.pageCount);notice(`已打开打印预览，${result.itemCount} 张票据/凭证，共 ${result.pageCount} 页。`);}
    else saveBlob(new Blob([result.bytes],{type:'application/pdf'}),name,`已生成打印 PDF：${result.itemCount} 张票据/凭证，共 ${result.pageCount} 页。`);
   }
  }catch(error){notice(error.message);if(win?.isConnected){win.close();}}
  finally{working=false;render();}
 }
 async function importProofs(selected){if(selected.length>50){notice('每次最多选择 50 个凭证文件，请分批添加');return;}if(working||invoiceBusy){notice('附件仍在处理，请稍候再添加凭证');return;}working=true;render();let added=0,duplicates=0;const errors=[];try{for(const file of selected)try{(await addFile(file,'proof'))?added++:duplicates++;}catch(error){errors.push(file.name+'：'+error.message);}}finally{working=false;render();el('proof-files').value='';notice(`已添加 ${added} 个凭证${duplicates?`，跳过 ${duplicates} 个重复凭证`:''}。\n`+errors.join('\n'));}}
 const drop=el('proof-dropzone'),picker=el('proof-files');drop.onclick=e=>{if(e.target!==picker)picker.click();};drop.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();picker.click();}};for(const event of ['dragenter','dragover'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('dragging');});drop.ondragleave=()=>drop.classList.remove('dragging');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('dragging');importProofs([...e.dataTransfer.files]);};picker.onchange=e=>importProofs([...e.target.files]);el('download-originals').onclick=()=>run('zip');el('download-print-pdf').onclick=()=>run('pdf');el('print-attachments').onclick=()=>{const preview=document.createElement('dialog');preview.className='print-preview';preview.setAttribute('aria-label','发票和凭证打印预览');preview.textContent='正在准备发票和凭证，请稍候…';preview.onclose=()=>preview.remove();document.body.append(preview);preview.showModal();run('print',preview);};
 return {ready,list,addFile,remove:removeFile,clear,isBusy:()=>working,setInvoiceBusy:value=>{invoiceBusy=value;render();}};
})();
