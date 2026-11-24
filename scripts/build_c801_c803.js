const fs=require('fs');
const {Document,Packer,Paragraph,TextRun,Table,TableRow,TableCell,WidthType,AlignmentType,VerticalAlign,BorderStyle}=require('docx');
const F={ascii:"Times New Roman",hAnsi:"Times New Roman",eastAsia:"標楷體"};
const r=(t,o={})=>new TextRun({text:t,font:F,size:o.size||24,bold:!!o.bold});
const P=(t,o={})=>new Paragraph({alignment:o.align,spacing:{before:o.before||0,after:o.after||0},children:Array.isArray(t)?t:[r(t,o)]});
const W=9638; // A4 可用寬（2 cm 邊界）
const cell=(t,w,o={})=>new TableCell({width:{size:w,type:WidthType.DXA},columnSpan:o.span,rowSpan:o.rowSpan,verticalAlign:VerticalAlign.CENTER,
  margins:{top:60,bottom:60,left:100,right:100},children:(Array.isArray(t)?t:[t]).map(x=>typeof x==='string'?P(x,{align:o.align,bold:o.bold,size:o.size}):x)});
const tbl=(cols,rows)=>new Table({width:{size:W,type:WidthType.DXA},columnWidths:cols,rows});
const page={size:{width:11906,height:16838},margin:{top:1134,bottom:1134,left:1134,right:1134}};
const BLANK="（請自行填寫）";
// ---------- C801
const c=[1000,1900,3000,1400,2338];
const c801=[
 P("國家科學及技術委員會",{align:AlignmentType.CENTER,bold:true,size:32}),
 P("116 年度大專學生研究計畫申請書",{align:AlignmentType.CENTER,bold:true,size:32,after:200}),
 P("一、綜合資料：",{bold:true,after:100}),
 tbl(c,[
  new TableRow({children:[cell("申請人（學生）",c[0],{rowSpan:6,align:AlignmentType.CENTER}),cell("姓名",c[1]),cell("程陳宥",c[2]),cell("身分證號碼",c[3]),cell(BLANK,c[4])]}),
  new TableRow({children:[cell("就讀學校、科系及年級",c[1]),cell("淡江大學 資訊管理學系 二年級",c[2]),cell("電話",c[3]),cell(BLANK,c[4])]}),
  new TableRow({children:[cell("學生研究計畫名稱",c[1]),cell("具不確定性感知與人工覆核機制之長照取藥機械手臂研究",c[2]+c[3]+c[4],{span:3})]}),
  new TableRow({children:[cell("研究期間",c[1]),cell("自 116 年 7 月 1 日至 117 年 2 月底止，計 8 個月",c[2]+c[3]+c[4],{span:3})]}),
  new TableRow({children:[cell("計畫歸屬處別",c[1]),cell("□自然處　□工程處　□生科處　□人文處（待與指導教授確認）",c[2]+c[3]+c[4],{span:3})]}),
  new TableRow({children:[cell("研究學門代碼及名稱",c[1]),cell("（待與指導教授確認）",c[2]+c[3]+c[4],{span:3})]}),
  new TableRow({children:[cell("上年度曾執行本會大專學生研究計畫",c[0]+c[1],{span:2}),cell("□是（計畫編號：NSTC　　　－　　－　　－　　－　　）　■否",c[2]+c[3]+c[4],{span:3})]}),
  new TableRow({children:[cell("指導教授",c[0],{rowSpan:3,align:AlignmentType.CENTER}),cell("姓名",c[1]),cell("徐煥志",c[2]),cell("身分證號碼",c[3]),cell("（由指導教授填寫）",c[4])]}),
  new TableRow({children:[cell("服務機構及科系（所）",c[1]),cell("淡江大學 資訊管理學系",c[2]+c[3]+c[4],{span:3})]}),
  new TableRow({children:[cell("職稱",c[1]),cell("（待確認）",c[2]),cell("電話",c[3]),cell("（由指導教授填寫）",c[4])]}),
 ]),
 P("",{after:100}),
 tbl([2600,2000,5038],[
  new TableRow({children:[cell("補助經費項目",2600,{align:AlignmentType.CENTER,bold:true}),cell("金額",2000,{align:AlignmentType.CENTER,bold:true}),cell("說明",5038,{align:AlignmentType.CENTER,bold:true})]}),
  new TableRow({children:[cell("研究助學金",2600),cell("48,000 元",2000,{align:AlignmentType.CENTER}),cell("每月補助研究助學金新臺幣 6,000 元，8 個月計新臺幣 48,000 元。",5038)]}),
  new TableRow({children:[cell("耗材、物品、圖書及雜項費用",2600),cell("＿＿＿＿ 元",2000,{align:AlignmentType.CENTER}),cell("依表 C803 合計填寫；依研究計畫實際需求擇優補助，每一計畫最高以補助新臺幣 20,000 元為限。",5038)]}),
  new TableRow({children:[cell("合計",2600,{align:AlignmentType.CENTER,bold:true}),cell("＿＿＿＿ 元",2000,{align:AlignmentType.CENTER}),cell("",5038)]}),
 ]),
 P("",{after:200}),
 P("科、系主管姓名：＿＿＿＿＿＿＿＿（學生就讀學校）",{align:AlignmentType.RIGHT,after:200}),
 P("表 C801",{align:AlignmentType.RIGHT,bold:true,size:22}),
];
// ---------- C803
const k=[2500,3238,700,700,1000,800,700];
const item=(n,d,u,q)=>new TableRow({children:[cell(n,k[0]),cell(d,k[1]),cell(u,k[2],{align:AlignmentType.CENTER}),cell(q,k[3],{align:AlignmentType.CENTER}),cell("待查",k[4],{align:AlignmentType.CENTER}),cell("",k[5]),cell("",k[6])]});
const c803=[
 P("三、耗材、物品、圖書及雜項費用：",{bold:true,after:100}),
 P("（一）凡執行研究計畫所需之耗材、物品、圖書及雜項費用，均可填入本表內。"),
 P("（二）說明欄請就該項目之規格、用途等相關資料詳細填寫，以利審查。"),
 P("（三）依研究計畫實際需求擇優補助，每一計畫最高以補助新臺幣 20,000 元為限。",{after:100}),
 P("金額單位：新臺幣元",{align:AlignmentType.RIGHT,size:22,after:60}),
 tbl(k,[
  new TableRow({children:["項目名稱","說明","單位","數量","單價","金額","備註"].map((h,i)=>cell(h,k[i],{align:AlignmentType.CENTER,bold:true,size:22}))}),
  item("短距離 RGB-D 相機","手腕 RGB-D 驗證用；最近對焦約 7 cm 的短距離深度相機（如 Intel RealSense D405 同級）。用於第 5 月拍攝模型藥丸，建立空杯、群聚與掉落測試資料（計畫書 4.4、4.7 節）。","台","1"),
  item("無藥性模型藥丸","圓錠與膠囊造型、多種顏色，不含任何藥物成分；供真實 RGB-D 計數與空杯判讀測試，避免使用真實藥物（計畫書 2.5、4.8 節）。","批","1"),
  item("開口藥杯／分格藥盒","與模擬場景尺寸相近的開口分格藥杯，貼上顏色標籤與識別黑點，用於真實影像測試。","個","6"),
  item("USB 指向性麥克風","匿名固定語句錄音（25 句）用，評估真人語音的字錯誤率與意圖辨識（計畫書 4.4、4.8 節）。","支","1"),
  item("紀錄與印刷費","受試者同意書、實驗紀錄表印製與文具。","批","1"),
  new TableRow({children:[cell("合計",k[0]+k[1]+k[2]+k[3]+k[4],{span:5,align:AlignmentType.CENTER,bold:true}),cell("",k[5]),cell("",k[6])]}),
 ]),
 P("",{after:200}),
 P("表 C803",{align:AlignmentType.RIGHT,bold:true,size:22}),
];
const mk=(ch,f)=>Packer.toBuffer(new Document({styles:{default:{document:{run:{font:F,size:24}}}},sections:[{properties:{page},children:ch}]})).then(b=>fs.writeFileSync(f,b));
Promise.all([mk(c801,process.argv[2]),mk(c803,process.argv[3])]).then(()=>console.log('ok'));
