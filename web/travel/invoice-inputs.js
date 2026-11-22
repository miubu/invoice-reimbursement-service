'use strict';
(function(root){
 const supported=/\.(pdf|jpe?g|png|webp)$/i,maxFile=20*1024*1024,maxTotal=200*1024*1024;
 async function expand(files,onNotice=()=>{}){
  const output=[];let total=0;
  function add(file){if(!file.size||file.size>maxFile)throw new Error(file.name+'：文件须为非空且不超过 20MB');total+=file.size;if(total>maxTotal)throw new Error('解压后文件合计超过 200MB');if(output.length>=100)throw new Error('每批最多 100 个发票文件');output.push(file);}
  for(const file of files){
   if(!/\.zip$/i.test(file.name)){if(supported.test(file.name))add(file);else onNotice(file.name+'：已跳过不支持的文件');continue;}
   if(!file.size||file.size>maxFile)throw new Error(file.name+'：ZIP 须为非空且不超过 20MB');
   const zip=await JSZip.loadAsync(await file.arrayBuffer(),{decodeFileName:bytes=>new TextDecoder('gbk').decode(bytes)});
   const entries=Object.values(zip.files).filter(entry=>!entry.dir);if(entries.length>1000)throw new Error(file.name+'：压缩包条目过多');let count=0;
   for(const entry of entries){
    const path=entry.unsafeOriginalName||entry.name,normalized=path.replace(/\\/g,'/');
    if(normalized.startsWith('/')||/^[A-Za-z]:/.test(normalized)||normalized.split('/').includes('..'))throw new Error(file.name+'：压缩包包含不安全路径');
    if(!supported.test(path)){onNotice(file.name+' / '+path+'：已跳过不支持的文件');continue;}
    if(output.length>=100)throw new Error('每批最多 100 个发票文件');
    // Bound decompression while streaming, rather than trusting the ZIP size header.
    const bytes=await new Promise((resolve,reject)=>{const chunks=[];let size=0,failed=false;const stream=entry.internalStream('uint8array');stream.on('data',chunk=>{if(failed)return;size+=chunk.length;if(size>maxFile||total+size>maxTotal){failed=true;stream.pause();reject(new Error(file.name+' / '+path+'：解压后超过文件或批次大小限制'));return;}chunks.push(chunk);}).on('error',reject).on('end',()=>{if(failed)return;const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}resolve(bytes);}).resume();});
    add(new File([bytes],file.name.replace(/\.zip$/i,'')+'／'+path));count++;
   }
   if(!count)throw new Error(file.name+'：ZIP 中没有 PDF 或图片发票');
   onNotice(file.name+'：已在本机解压 '+count+' 个发票文件');
  }
  return output;
 }
 root.InvoiceInputs={expand};if(typeof module!=='undefined')module.exports=root.InvoiceInputs;
})(typeof window!=='undefined'?window:globalThis);
