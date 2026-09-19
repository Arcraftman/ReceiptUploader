// Static parsing only: never executes downloaded frontend JavaScript.
const fs = require('fs');
const path = require('path');
const acorn = require(process.argv[2]);
const manifest = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const output = process.argv[4];
const calls = [], failures = [];
const endpointPattern = /^(?:https?:\/\/[^/]+)?\/[A-Za-z][^\s]*\//;
for (const file of manifest) {
  if (!file.file || file.error) continue;
  const src = fs.readFileSync(file.file, 'utf8');
  if (/^\s*</.test(src)) continue;
  let ast;
  try { ast = acorn.parse(src, {ecmaVersion:'latest', sourceType:'script', allowReturnOutsideFunction:true, locations:true}); }
  catch (e) { failures.push({file:file.file,error:e.message}); continue; }
  function name(n) { return n && (n.name || n.value); }
  function expr(n) {
    if (!n) return '';
    if (n.type === 'Literal') return typeof n.value === 'string' ? n.value : String(n.value);
    if (n.type === 'BinaryExpression' && n.operator === '+') return expr(n.left) + expr(n.right);
    if (n.type === 'TemplateLiteral') return n.quasis.map((q,i)=>q.value.cooked+(i<n.expressions.length?expr(n.expressions[i]):'')).join('');
    if (n.type === 'CallExpression' && n.callee.type === 'MemberExpression' && name(n.callee.property) === 'concat') return expr(n.callee.object)+n.arguments.map(expr).join('');
    return '{'+src.slice(n.start,n.end).slice(0,80)+'}';
  }
  function record(node,url,method,params,kind) {
    if (url.startsWith('../')) url='/' + url.replace(/^(?:\.\.\/)+/,'');
    if (/^(?:basedata|gl|bs|pay|fa|scm|report|acct)\//.test(url)) url='/'+url;
    if (url.startsWith('/jdy-fi')) url=url.replace(/^(\/jdy-fi[^/]*\/)\{[^}]+\}/,'$1{dbId}');
    if (!endpointPattern.test(url)) return;
    if (!['GET','POST','PUT','DELETE','PATCH','HEAD','UNKNOWN'].includes(method)) method='UNKNOWN';
    calls.push({method,url,kind,parameters_expression:params?src.slice(params.start,params.end).slice(0,600):'', source:file.url, file:file.file, line:node.loc.start.line, column:node.loc.start.column+1, evidence:src.slice(node.start,Math.min(node.end,node.start+900))});
  }
  function visit(n) {
    if (!n || typeof n !== 'object') return;
    if (n.type === 'CallExpression' && n.callee.type === 'MemberExpression') {
      let method = name(n.callee.property);
      if (['get','post','put','delete','patch','getJSON','postFormData','getFile','postFile'].includes(method) && n.arguments.length) {
        let url=expr(n.arguments[0]);
        // Vue's $http uses the /guanjia base path.
        if (src.slice(n.callee.start,n.callee.end).includes('$http') && !url.startsWith('/') && !url.startsWith('{') && url.includes('/')) url='/guanjia/'+url;
        record(n,url,method==='getJSON'||method==='getFile'?'GET':method.startsWith('post')?'POST':method.toUpperCase(),n.arguments[1],'http_call');
      }
    }
    if (n.type === 'ObjectExpression') {
      const props=Object.fromEntries(n.properties.filter(p=>p.type==='Property').map(p=>[name(p.key),p.value]));
      if (props.url) record(n,expr(props.url),String(name(props.method)||name(props.type)||'UNKNOWN').toUpperCase(),props.params||props.data,'request_config');
    }
    for(const [k,v] of Object.entries(n)) {
      if(k==='loc')continue;
      if(Array.isArray(v))v.forEach(visit);else if(v&&typeof v==='object')visit(v);
    }
  }
  visit(ast);
}
fs.writeFileSync(output,JSON.stringify({calls,parse_failures:failures},null,2));
console.log(JSON.stringify({calls:calls.length,parse_failures:failures.length}));
