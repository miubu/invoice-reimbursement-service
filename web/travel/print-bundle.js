'use strict';
(function(root){
 async function buildPdf(records,onProgress=()=>{}){
  if(!records.length)throw new Error('请先添加发票或凭证');
  const output=await PDFLib.PDFDocument.create(),width=595.28,height=841.89,margin=18,gap=18;
  let unfinishedProofSheet=null,itemCount=0;
  function place(item,kind,rotation=0,image=false){
   const proof=kind==='proof';
   const lowerSlot=proof && unfinishedProofSheet!==null;
   const sheet=lowerSlot ? unfinishedProofSheet : output.addPage([width,height]);
   const slotHeight=proof ? (height-2*margin-gap)/2 : height-2*margin;
   const bottom=proof && !lowerSlot ? margin+slotHeight+gap : margin;
   unfinishedProofSheet=proof && !lowerSlot ? sheet : null;
   const turn=((rotation%360)+360)%360,sideways=turn===90||turn===270,w=sideways?item.height:item.width,h=sideways?item.width:item.height;
   const scale=Math.min((width-2*margin)/w,slotHeight/h),x=(width-w*scale)/2,y=bottom+(slotHeight-h*scale)/2;
   const options={x:x+(turn===180||turn===270?w*scale:0),y:y+(turn===90||turn===180?h*scale:0),width:item.width*scale,height:item.height*scale,rotate:PDFLib.degrees(-turn)};
   if(image)sheet.drawImage(item,options);else sheet.drawPage(item,options);
   itemCount++;
  }
  for(let i=0;i<records.length;i++){
   const record=records[i];onProgress(`正在排版 ${i+1}/${records.length}：${record.name}`);
   try{
    if(/\.pdf$/i.test(record.name)){
     const source=await PDFLib.PDFDocument.load(await record.blob.arrayBuffer());
     if(!source.getPageCount())throw new Error('PDF 没有页面');
     for(const page of source.getPages()){
      if(!page.node.Contents())page.drawRectangle({x:0,y:0,width:0,height:0,opacity:0});
      const box=page.getCropBox(),embedded=await output.embedPage(page,{left:box.x,bottom:box.y,right:box.x+box.width,top:box.y+box.height});
      place(embedded,record.kind,page.getRotation().angle);
     }
    }else{
     const bitmap=await createImageBitmap(record.blob);
     try{
      if(bitmap.width*bitmap.height>40000000)throw new Error('图片超过 4000 万像素，请使用较小的图片');
      const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;
      try{
       const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0);
       const png=await new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('图片转换失败')),'image/png'));
       place(await output.embedPng(await png.arrayBuffer()),record.kind,0,true);
      }finally{canvas.width=canvas.height=0;}
     }finally{bitmap.close();}
    }
   }catch(error){throw new Error(`${record.name} 无法合并：${/encrypt|password/i.test(error.message)?'PDF 受密码保护，请先解锁':error.message}。未生成不完整的打印文件；仍可下载原始附件。`);}
  }
  return {bytes:await output.save(),pageCount:output.getPageCount(),itemCount};
 }
root.PrintBundle={buildPdf};if(typeof module!=='undefined')module.exports=root.PrintBundle;
})(typeof window!=='undefined'?window:globalThis);
