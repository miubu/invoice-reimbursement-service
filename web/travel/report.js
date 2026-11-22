/* Shared layout and parsing for the local form and its Excel output. */
(function(root){
const categories=['飞机','火车','汽车','住宿费','会议（会务）费','其他费用'];
const names=s=>String(s||'').split(/[、,，;；\n]+/).map(x=>x.trim()).filter(Boolean);
function cents(s){if(!/^\d+(\.\d{1,2})?$/.test(String(s)))return null;const [a,b='']=String(s).split('.');const n=Number(a)*100+Number(b.padEnd(2,'0'));return Number.isSafeInteger(n)&&n<=99999999999?n:null;}
function validDate(s){return /^\d{4}-\d{2}-\d{2}$/.test(s)&&!Number.isNaN(Date.parse(s))&&new Date(s+'T00:00:00Z').toISOString().slice(0,10)===s;}
function days(a,b){return validDate(a)&&validDate(b)?Math.round((Date.parse(b)-Date.parse(a))/86400000)+1:null;}
function pdfText(items){
 // PDF drawing order can put all labels before their values; rebuild visual rows.
 const rows=[];for(const item of items.filter(x=>typeof x.str==='string'&&x.str.trim()).sort((a,b)=>b.transform[5]-a.transform[5]||a.transform[4]-b.transform[4])){
  const y=item.transform[5],last=rows[rows.length-1];if(last&&Math.abs(y-last.y)<=2.5)last.items.push(item);else rows.push({y,items:[item]});
 }
 return rows.map(row=>row.items.sort((a,b)=>a.transform[4]-b.transform[4]).map(x=>x.str).join(' ')).join('\n');
}
function parseInvoice(text,filename=''){
 const t=String(text).replace(/[\s\u3000]/g,'');
 // ponytail: label-based local extraction; unfamiliar layouts stay blank for manual review.
 // Prefer the tax-inclusive total; the plain "合计" row can be the pre-tax subtotal.
 const explicit=[...t.matchAll(/(?:[（(]小写[）)]|价税合计(?:[（(]小写[）)])?|实付金额|支付金额|总金额|合计金额|票价合计)[:：]?[¥￥]?(\d[\d,]*\.\d{2})(?!\d)/g)];
 const money=(explicit.length?explicit:[...t.matchAll(/合计(?:CNY)?[¥￥](\d[\d,]*\.\d{2})(?!\d)/g)]).map(m=>m[1].replace(/,/g,''));
 const amounts=[...new Set(money)];const amount=amounts.length===1&&cents(amounts[0])!==null?amounts[0]:'';
 const dateMatches=[...t.matchAll(/(?:乘机日期|航班日期|出发日期|入住日期|行程信息|行程日期|开票日期|日期)[:：]?(20\d{2})[年\/.\-](\d{1,2})[月\/.\-](\d{1,2})日?/g)];
 const iso=match=>match?`${match[1]}-${match[2].padStart(2,'0')}-${match[3].padStart(2,'0')}`:'';
 const invoiceCandidate=iso(dateMatches.find(m=>/开票/.test(m[0]))),invoiceDate=validDate(invoiceCandidate)?invoiceCandidate:'';
 function readDate(token){const full=token.match(/^(20\d{2})[年\/.\-](\d{1,2})[月\/.\-](\d{1,2})日?$/),short=token.match(/^(\d{1,2})[月\/.\-](\d{1,2})日?$/);const candidate=full?iso(full):short&&invoiceDate?`${invoiceDate.slice(0,4)}-${short[1].padStart(2,'0')}-${short[2].padStart(2,'0')}`:'';return validDate(candidate)?candidate:'';}
 const stay=t.match(/(?:入离日期|入住日期|入住时间)[:：]?((?:20\d{2}[年\/.\-])?\d{1,2}[月\/.\-]\d{1,2}日?)(?:至|到|[~～—－])((?:20\d{2}[年\/.\-])?\d{1,2}[月\/.\-]\d{1,2}日?)/);
 const flight=t.match(/[\/／]((?:20\d{2}[年\/.\-])?\d{1,2}[月\/.\-]\d{1,2}日?)[\/／]([A-Z0-9]{2}\d{3,4})(?=[)）\/／]|$)/i);
 const first=dateMatches.find(m=>/乘机|航班|出发|入住|行程/.test(m[0]))||dateMatches[0];
 let date=iso(first),dateSource=first&&/开票/.test(first[0])?'开票日期':first?'费用日期':'',dateLabel=dateSource,dateInferred=false,checkoutDate='',nights='';
 if(stay&&readDate(stay[1])){date=readDate(stay[1]);checkoutDate=readDate(stay[2]);dateSource='费用日期';dateLabel='入住日期';dateInferred=!/^20\d{2}/.test(stay[1]);if(checkoutDate){const duration=days(date,checkoutDate)-1;if(duration>=0)nights=String(duration);else checkoutDate='';}}
 else if(flight&&readDate(flight[1])){date=readDate(flight[1]);dateSource='费用日期';dateLabel='乘机日期';dateInferred=!/^20\d{2}/.test(flight[1]);}
 if(!validDate(date)){date='';dateSource='';dateLabel='';}
 const descriptions=[...String(text).matchAll(/[*＊]([^*＊\r\n]{2,30})[*＊]([^*＊\r\n]{0,60})/g)].map(m=>m[1]+m[2]).join('');
 const billed=[];if(/住宿|酒店|客房|房费/.test(descriptions))billed.push('住宿费');if(/机票|航空|民航|乘机|燃油附加/.test(descriptions))billed.push('飞机');if(/铁路|火车|高铁|动车/.test(descriptions))billed.push('火车');if(/汽车客运|长途汽车/.test(descriptions))billed.push('汽车');if(/会议|会务|注册费/.test(descriptions))billed.push('会议（会务）费');
 const classify=s=>/住宿|酒店|宾馆|客房/.test(s)?'住宿费':/航空|机票|乘机|航班|行程单/.test(s)?'飞机':/铁路|火车|高铁|动车/.test(s)?'火车':/会议|会务|注册费/.test(s)?'会议（会务）费':/汽车客运|长途汽车/.test(s)?'汽车':'';
 // Billed services outrank company names and archive filenames.
 const category=billed.length===1?billed[0]:billed.length>1?'':classify(t)||classify(String(filename).split(/[\\/／]/).pop());
 const number=(t.match(/发票号码[:：]?(\d{8,20})(?!\d)/)||[])[1]||'';
 return {category,amount,date,number,dateSource,dateLabel,dateInferred,invoiceDate,checkoutDate,nights,missing:[!category&&'类别',!amount&&'金额',!date&&'日期'].filter(Boolean)};
}
function layout(data={}){
 const expenses=data.expenses||[],payments=data.payments||[];const n=Math.max(7,expenses.length),m=Math.max(4,payments.length),totalRow=14+n,payTitle=totalRow+1,payHead=payTitle+1,payStart=payHead+1,last=payStart+m;
 const rows=Array.from({length:last},()=>Array(7).fill(null));const merges=[],blue=[],red=[],formulas=[];
 const put=(r,c,v)=>rows[r-1][c-1]=v;const merge=(range,value)=>{merges.push(range);put(Number(range.match(/\d+/)[0]),range.charCodeAt(0)-64,value);};
 merge('A1:G1',(data.names?names(data.names).join('、'):'')+'差旅报销');merge('A2:G2','说明：');merge('A3:G3','1、严格按照表格内容填写；');merge('A4:G4','2、仅手动填写白色部分，浅蓝色固定区域请勿修改；');merge('A5:G5','3、文件名称：姓名－地点－出差起始日期（YYYYMMDD）');red.push('A5:G5');
 merge('A6:A7','出差日期');put(6,2,'出差起始日期');put(6,3,'出差终止日期');put(7,2,data.start?new Date(data.start+'T00:00:00Z'):null);put(7,3,data.end?new Date(data.end+'T00:00:00Z'):null);merge('D6:G7',null);formulas.push(['D6','=IF(OR(B7="",C7=""),"","出差共"&(C7-B7+1)&"天")']);blue.push('A6:A7','B6:C6','D6:G7');
 merge('B8:G8',data.reason||null);put(8,1,'出差事由');merge('B9:F9',data.names?names(data.names).join('、'):null);put(9,1,'出差人姓名');put(9,7,'顿号隔开');put(10,1,'出差人数');put(10,2,data.names?names(data.names).length:null);merge('C10:G10','和出差人姓名数一致');put(11,1,'职称职别');put(11,2,data.role||null);merge('C11:G11','教授  副教授  博士后  学生');put(12,1,'出差地点');merge('B12:F12',data.destination||null);put(12,7,'精确到市');blue.push('A8:A12','G9','C10:G11','G12');red.push('G9','C10:G11','G12');
 rows[12]=['类别','人数','费用发生日期','天数','票据张数','票据合计金额','说明'];blue.push('A13:G13');
 for(let i=0;i<n;i++){const x=expenses[i];if(x)rows[13+i]=[x.category,Number(x.people)||null,x.date?new Date(x.date+'T00:00:00Z'):null,x.days===''?null:Number(x.days),Number(x.tickets)||null,cents(x.amount)/100,x.note||null];else if(i===4){put(14+i,1,'住宿费');put(14+i,7,'教师和学生请分别开票');}else if(i===5){put(14+i,1,'会议（会务）费');put(14+i,7,'会议注册费用');}else if(i===6){put(14+i,1,'其他费用');put(14+i,7,'打车费、高速过路费等');}blue.push(`G${14+i}`);}
 merge(`A${totalRow}:D${totalRow}`,'费用合计');formulas.push([`E${totalRow}`,`=SUM(E14:E${totalRow-1})`],[`F${totalRow}`,`=SUM(F14:F${totalRow-1})`]);blue.push(`A${totalRow}:G${totalRow}`);merge(`A${payTitle}:E${payTitle}`,'打款信息');merge(`F${payTitle}:G${payHead}`,'仔细核对打款信息，务必保证准确无误！');blue.push(`A${payTitle}:G${payHead}`);red.push(`F${payTitle}:G${payHead}`);rows[payHead-1].splice(0,5,'序号','领款人','学号','电话','金额');
 for(let i=0;i<m;i++){const x=payments[i]||{};rows[payStart-1+i]=[i+1,x.name||null,x.student||null,x.phone||null,x.amount?cents(x.amount)/100:null,null,null];merge(`F${payStart+i}:G${payStart+i}`,'仔细核对打款信息，务必保证准确无误！');blue.push(`F${payStart+i}:G${payStart+i}`);red.push(`F${payStart+i}:G${payStart+i}`);}
 merge(`A${last}:D${last}`,'打款合计');formulas.push([`E${last}`,`=SUM(E${payStart}:E${last-1})`]);merge(`F${last}:G${last}`,'与费用合计一致');blue.push(`A${last}:G${last}`);
 return {rows,merges,blue,red,formulas,n,m,totalRow,payTitle,payHead,payStart,last,widths:[17,17,20,15,17,24,40]};
}
root.Report={categories,names,cents,days,validDate,pdfText,parseInvoice,layout};if(typeof module!=='undefined')module.exports=root.Report;
})(typeof window!=='undefined'?window:globalThis);
