import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [snapshotPath, outputDir] = process.argv.slice(2);
if (!snapshotPath || !outputDir) throw new Error('Usage: build_workbook.mjs snapshot.json output-directory');
const snapshot = JSON.parse(await fs.readFile(snapshotPath, 'utf8'));
const wb = Workbook.create();
const sheets = {};
for (const name of ['控制台','经营统计','预算输入','预算对比','账龄输入','账龄统计',...Object.keys(snapshot.sheets),'使用说明']) {
  const s = sheets[name] = wb.worksheets.add(name);
  s.showGridLines = false;
  s.getRange('A1:N40').format.font.name = 'Arial';
  s.getRange('A1:N40').format.font.size = 11;
  s.getRange('A1:N40').format.rowHeight = 23;
  s.getRange('A1:N40').format.columnWidth = 18;
}
const fmt = '#,##0.00;[Red](#,##0.00);"—"';
function title(s, text, note, headers) {
  s.getRange('A1').values = [[text]];
  s.getRange('A1').format.font.size = 18;
  s.getRange('A1').format.font.bold = true;
  s.getRange('A2').values = [[note]];
  if (headers) {
    const r = s.getRangeByIndexes(3,0,1,headers.length);
    r.values = [headers]; r.format.fill = '#263D56'; r.format.font.color = '#FFFFFF'; r.format.font.bold = true;
    r.format.rowHeight = 30;
    s.freezePanes.freezeRows(4);
  }
}
function formula(s, cell, value) { s.getRange(cell).formulas = [[value]]; }
function safe(value) {return typeof value === 'string' && /^[=+@-]/.test(value) ? "'"+value : value;}
for (const [name,rows] of Object.entries(snapshot.sheets)) {
  const s=sheets[name], width=Math.max(...rows.map(r=>r.length));
  s.getRangeByIndexes(0,0,rows.length,width).setNumberFormat("@");
  s.getRangeByIndexes(0,0,rows.length,width).values=rows.map(row=>Array.from({length:width},(_,i)=>safe(row[i]??null)));
  title(s,rows[0][0],rows[1][0],rows[3]);
  s.getRangeByIndexes(4,0,Math.max(1,rows.length-4),width).setNumberFormat(fmt);
  // Identifier and description columns must remain text.
  const textCols = {'刷新信息':[0,1],'公司列表':[0,1],'利润表':[0,1,5],'资产负债表':[0,3],'现金流量表':[0,1,2],
    '科目余额':[0,1],'凭证明细':[0,2,3,4,5,6,7,10,12],'出纳账':[0,2,3,4,5,6,7,10,12],
    '往来余额':[0,1,2,3],'月度趋势':[0,1,2,4]}[name];
  for (const c of textCols) s.getRangeByIndexes(4,c,Math.max(1,rows.length-4),1).setNumberFormat('@');
  if (name==='凭证明细'||name==='出纳账') {
    s.getRange('A1:A504').format.columnWidth=23;
    if (rows.length > 4) s.getRangeByIndexes(4,2,rows.length-4,1).values=rows.slice(4).map(r=>[new Date(r[2]+'T00:00:00Z')]);
    s.getRangeByIndexes(4,2,Math.max(1,rows.length-4),1).setNumberFormat('yyyy-mm-dd'); s.getRange('F1:H504').format.columnWidth=30;
  } else if (name==='往来余额') {s.getRange('B1:B504').format.columnWidth=43; s.getRange('D1:D504').format.columnWidth=43;}
  else if (name==='利润表') s.getRange('B1:B504').format.columnWidth=62;
  else if (name==='现金流量表') s.getRange('C1:C504').format.columnWidth=64;
  else if (name==='资产负债表') {s.getRange('A1:A504').format.columnWidth=42;s.getRange('D1:D504').format.columnWidth=42;}
  else s.getRange('B1:B504').format.columnWidth=45;
}
const home=sheets['控制台'];
title(home,'财务管理工作簿','选择公司和月份后点击刷新；金额默认人民币元。');
home.getRange('A4:B8').values=[['公司编号',snapshot.company],['月份',snapshot.month],['数据状态',null],['刷新结果',snapshot.templateOnly?'尚未读取数据':'已载入接口实测样本'],['最近刷新（UTC）',snapshot.refreshed_at?new Date(snapshot.refreshed_at):null]];
home.getRange('B4:B5').format.fill='#FFF2CC'; home.getRange('B4:B5').format.font.color='#0000FF'; home.getRange('B4:B5').setNumberFormat('@');
home.getRange('B1:B504').format.columnWidth=48;
home.getRange('B8').setNumberFormat('yyyy-mm-dd hh:mm:ss');
formula(home,'B6',`=IF(OR(B4="",B5=""),"请填写公司编号和月份",IF(AND(B4='刷新信息'!B6,B5='刷新信息'!B8),"当前选择与数据一致","选择已变化，请刷新"))`);
home.getRange('A11:B17').values=[['工作表','用途'],['经营统计','利润、现金收支、月度趋势'],['预算输入 / 预算对比','按公司、月份、利润项目录入预算'],['账龄输入 / 账龄统计','补充未核销单据；余额差额单列'],['出纳账','总账现金及银行凭证明细'],['刷新信息','数据身份、更新时间及原系统校验字段'],['使用说明','一次性安装与口径说明']];
home.getRange('A20').values=[['首次使用：在 Windows 运行安装脚本生成带刷新按钮的 .xlsm 文件。']];
home.getRange('A22').values=[['黄色区域为人工输入；刷新不会覆盖预算和账龄输入。']];
const matches=`AND('控制台'!B4<>"",'控制台'!B5<>"",'控制台'!B4='刷新信息'!B6,'控制台'!B5='刷新信息'!B8)`;
const stats=sheets['经营统计'];
title(stats,'经营统计','仅在选择与已刷新数据一致时显示；收支采用总账口径。',['指标','金额（元）']);
const metrics=[['营业收入',`SUMIFS('利润表'!C5:C200,'利润表'!F5:F200,"营业收入")`],
 ['营业成本',`SUMIFS('利润表'!C5:C200,'利润表'!F5:F200,"营业成本")`],
 ['净利润',`SUMIFS('利润表'!C5:C200,'利润表'!F5:F200,"净利润")`],
 ['现金银行流入',`SUMIFS('出纳账'!I5:I6000,'出纳账'!M5:M6000,"<>内部划转")`],
 ['现金银行流出',`SUMIFS('出纳账'!J5:J6000,'出纳账'!M5:M6000,"<>内部划转")`],
 ['现金银行净流入','B8-B9'],
 ['应收账款净额',`SUMIFS('往来余额'!I5:I6000,'往来余额'!A5:A6000,"应收")-SUMIFS('往来余额'!J5:J6000,'往来余额'!A5:A6000,"应收")`],
 ['应付账款净额',`SUMIFS('往来余额'!J5:J6000,'往来余额'!A5:A6000,"应付")-SUMIFS('往来余额'!I5:I6000,'往来余额'!A5:A6000,"应付")`]];
metrics.forEach(([name,f],i)=>{stats.getRange(`A${i+5}`).values=[[name]];formula(stats,`B${i+5}`,`=IF(${matches},${f},"")`);});
stats.getRange('B5:B12').setNumberFormat(fmt);
stats.getRange('A15:D15').values=[['月份','营业收入','营业成本','净利润']];
for(let m=1;m<=12;m++) {
 const row=m+15;formula(stats,`A${row}`,`=IF('控制台'!B5="","",LEFT('控制台'!B5,4)&"-${String(m).padStart(2,'0')}")`);
 ['营业收入','营业成本','净利润'].forEach((label,i)=>formula(stats,`${'BCD'[i]}${row}`,`=IF(AND(${matches},COUNTIF('月度趋势'!A5:A1000,A${row})>0),SUMIFS('月度趋势'!D5:D1000,'月度趋势'!A5:A1000,A${row},'月度趋势'!E5:E1000,"${label}"),"")`));
}
stats.getRange('B16:D27').setNumberFormat(fmt);
stats.getRange('A1:A504').format.columnWidth=24;
const chart=stats.charts.add('line',stats.getRange('A15:C27'));
chart.title='营业收入与成本（元）';chart.setPosition('F4','N19');
const input=sheets['预算输入'];
title(input,'预算输入','每行对应公司、月份、利润项目编码；同一组合可分笔录入，按合计比较。',['公司编号','月份','利润项目编码','项目名称','预算金额','说明']);
input.getRange('A5:F204').format.fill='#FFF8E5';input.getRange('A5:F204').format.font.color='#0000FF';input.getRange('E5:E204').setNumberFormat(fmt);
input.getRange('A1:C504').setNumberFormat('@');input.getRange('D1:D504').format.columnWidth=48;
const budget=sheets['预算对比'];title(budget,'预算对比','实际－预算；费用正差额表示超支。未录入预算时差额留空。',['项目编码','利润项目','实际金额','预算金额','差额','差异率']);
budget.getRange('B1:B504').format.columnWidth=62;
for(let r=5;r<37;r++) {
 formula(budget,`A${r}`,`=IF('利润表'!A${r}="","",'利润表'!A${r})`);formula(budget,`B${r}`,`=IF('利润表'!B${r}="","",'利润表'!B${r})`);
 formula(budget,`C${r}`,`=IF(AND(${matches},ISNUMBER('利润表'!C${r})),'利润表'!C${r},"")`);
 formula(budget,`D${r}`,`=IF(AND(${matches},COUNTIFS('预算输入'!A5:A204,'控制台'!B4,'预算输入'!B5:B204,'控制台'!B5,'预算输入'!C5:C204,A${r})>0),SUMIFS('预算输入'!E5:E204,'预算输入'!A5:A204,'控制台'!B4,'预算输入'!B5:B204,'控制台'!B5,'预算输入'!C5:C204,A${r}),"")`);
 formula(budget,`E${r}`,`=IF(OR(D${r}="",C${r}=""),"",C${r}-D${r})`);
 formula(budget,`F${r}`,`=IF(OR(D${r}="",D${r}=0),"",E${r}/ABS(D${r}))`);
}
budget.getRange('C5:E36').setNumberFormat(fmt);budget.getRange('F5:F36').setNumberFormat('0.0%');
const age=sheets['账龄输入'];title(age,'未核销单据输入','金额为截至所选月末仍未核销的人民币余额；不是发票原始总额。项目ID从往来余额复制。',['公司编号','月份','应收/应付','核算项目ID组合','单据编号','发生日期','到期日期','未核销金额','账龄天数','逾期天数','核验']);
age.getRange('A5:H504').format.fill='#FFF8E5';age.getRange('A5:H504').format.font.color='#0000FF';age.getRange('A1:E504').setNumberFormat('@');age.getRange('F5:G504').setNumberFormat('yyyy-mm-dd');age.getRange('H5:H504').setNumberFormat(fmt);age.getRange('D1:D504').format.columnWidth=44;
age.getRange('C5:C504').dataValidation={rule:{type:'list',values:['应收','应付']}};
const asof=`DATE(VALUE(LEFT('控制台'!B5,4)),VALUE(RIGHT('控制台'!B5,2))+1,1)-1`;
for(let r=5;r<=504;r++) {
 formula(age,`I${r}`,`=IF(OR(A${r}<> '控制台'!B4,B${r}<> '控制台'!B5,F${r}=""),"",${asof}-F${r})`);
 formula(age,`J${r}`,`=IF(OR(A${r}<> '控制台'!B4,B${r}<> '控制台'!B5,G${r}=""),"",MAX(0,${asof}-G${r}))`);
 formula(age,`K${r}`,`=IF(A${r}="","",IF(AND(${matches},A${r}='控制台'!B4,B${r}='控制台'!B5,OR(C${r}="应收",C${r}="应付"),COUNTIFS('往来余额'!A5:A6000,C${r},'往来余额'!B5:B6000,D${r})=1,E${r}<>"",COUNTIFS(A$5:A$504,A${r},B$5:B$504,B${r},C$5:C$504,C${r},D$5:D$504,D${r},E$5:E$504,E${r})=1,ISNUMBER(F${r}),I${r}>=0,ISNUMBER(H${r}),H${r}>=0),"纳入","待核实"))`);
}
const aging=sheets['账龄统计'];title(aging,'账龄统计','按发生日计算账龄；逾期按到期日计算。未分配金额不推断账龄。',['类型','往来净余额','已分配单据','未分配净额','0–30天','31–60天','61–90天','91–180天','181–365天','365天以上','逾期金额']);
aging.getRange('A5:A6').values=[['应收'],['应付']];
for(let r=5;r<=6;r++) {
 formula(aging,`B${r}`,`=IF(${matches},'经营统计'!B${r+6},"")`);
 const sumBase=`'账龄输入'!H5:H504,'账龄输入'!C5:C504,A${r},'账龄输入'!K5:K504,"纳入"`;
 formula(aging,`C${r}`,`=IF(${matches},SUMIFS(${sumBase}),"")`);
 formula(aging,`D${r}`,`=IF(${matches},B${r}-C${r},"")`);
 [[0,30],[31,60],[61,90],[91,180],[181,365],[366,999999]].forEach(([lo,hi],i)=>formula(aging,`${'EFGHIJ'[i]}${r}`,`=IF(${matches},SUMIFS(${sumBase},'账龄输入'!I5:I504,">=${lo}",'账龄输入'!I5:I504,"<=${hi}"),"")`));
 formula(aging,`K${r}`,`=IF(${matches},SUMIFS(${sumBase},'账龄输入'!J5:J504,">0"),"")`);
}
aging.getRange('B5:K6').setNumberFormat(fmt);
aging.getRange('A9').values=[['未分配净额包括未录入单据及预收预付抵减；不等于已核销差异，也不等于逾期金额。']];
const guide=sheets['使用说明'];title(guide,'使用说明','一次性安装按钮后，日常只需选择公司、月份并刷新。',['事项','说明']);
guide.getRange('A5:B15').values=[['连接','Windows 通过 SSH 转发连接 Linux 的只读服务；账无忧会话只保存在后台。'],['首次安装','运行 Install-Finance.ps1，将此 .xlsx 转为 .xlsm 并加入真实刷新按钮。'],['输入保护','刷新只替换原始数据表；预算输入、账龄输入及其公式不会被覆盖。'],['容量','每张接口表最多 6000 行；预算输入 200 行、账龄输入 500 行。超限需要扩展公式。'],['账龄','当前未接入单据核销与到期日接口；人工补充未核销单据后计算，不伪造自动账龄。'],['出纳','总账1001/1002科目；仅现金银行科目之间的凭证识别为内部划转。复合凭证仍需复核。'],['利润口径','利润表本月balance、本年累计curBalance、上年累计preBalance；以账无忧报表为准。'],['现金流字段','保留原接口字段名，字段期间口径需对照账套现金流量表；不将未知字段重命名。'],['异常','失败时保留旧数据；更换选择但未刷新时统计页留空；原系统校验信息在刷新信息中。'],['参考结构','https://www.vertex42.com/ExcelTemplates/financial-statements.html'],['参考范围','https://www.smartsheet.com/content/free-business-templates']];
guide.getRange('A1:A504').format.columnWidth=20;guide.getRange('B1:B504').format.columnWidth=110;guide.getRange('B5:B15').format.wrapText=true;guide.getRange('A5:B15').format.rowHeight=42;
// Exercise editable inputs against a populated sample; the distributed template contains no business rows.
if (!snapshot.templateOnly) {
const revenue = snapshot.sheets['利润表'].slice(4).find(r=>r[5]==='营业收入');
input.getRange('A5:F5').values=[[snapshot.company,snapshot.month,revenue[0],revenue[1],3000000,'authoring check']];
let test=await wb.inspect({kind:'table',range:'预算对比!C5:F5',include:'values',tableMaxRows:1,tableMaxCols:4});
let result=JSON.parse(test.ndjson.trim().split('\n')[0]).values[0];
if (Math.abs(result[2]-(revenue[2]-3000000))>0.005 || result[1]!==3000000) throw new Error('Budget input calculation failed: '+test.ndjson);
input.getRange('A5:F5').clear({applyTo:'contents'});
const party=snapshot.sheets['往来余额'].slice(4).find(r=>r[0]==='应收');
age.getRange('A5:H5').values=[[snapshot.company,snapshot.month,'应收',party[1],'TEST-AGE',new Date(snapshot.month+'-01T00:00:00Z'),new Date(snapshot.month+'-15T00:00:00Z'),123.45]];
test=await wb.inspect({kind:'table',range:'账龄统计!C5:K5',include:'values',tableMaxRows:1,tableMaxCols:9});
result=JSON.parse(test.ndjson.trim().split('\n')[0]).values[0];
if(Math.abs(result[0]-123.45)>0.005||Math.abs(result[8]-123.45)>0.005) throw new Error('Aging input calculation failed: '+test.ndjson);
age.getRange('A5:H5').clear({applyTo:'contents'});
}
await fs.mkdir(outputDir,{recursive:true});
console.log((await wb.inspect({kind:'table',range:'经营统计!A4:B12',include:'values,formulas',tableMaxRows:10,tableMaxCols:2})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',options:{useRegex:true,maxResults:20},summary:'formula errors'})).ndjson);
for (const [name,s] of Object.entries(sheets)) {
 const end=name==='经营统计'?'N28':name==='账龄统计'?'K12':name==='账龄输入'?'K10':name==='使用说明'?'B15':name==='出纳账'||name==='凭证明细'?'M10':name==='往来余额'?'J10':name==='控制台'?'G23':name==='科目余额'?'I10':name==='现金流量表'?'F10':name==='刷新信息'?'B24':'F12';
 const blob=await wb.render({sheetName:name,range:`A1:${end}`,scale:1});
 await fs.writeFile(path.join(outputDir,`preview-${name}.png`),new Uint8Array(await blob.arrayBuffer()));
}
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(outputDir,'财务管理模板.xlsx'));
console.log('Workbook exported');
