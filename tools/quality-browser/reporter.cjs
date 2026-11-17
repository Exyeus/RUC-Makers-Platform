const fs=require('fs'),path=require('path'),cp=require('child_process');
class Reporter {
 onBegin(){this.checks=[];this.identity=JSON.parse(fs.readFileSync(process.env.QA_OUTPUT+'/identity.json'));}
 onTestEnd(test,result){
 const id=test.title.match(/^(AUTH|TEAM|SUBMIT|OPS)-\d+/)?.[0]; if(!id)return;
 const toolBlocked=result.errors.some(e=>/Executable doesn't exist|browserType.launch/.test(e.message||''));
 const assertions=Object.fromEntries(test.annotations.filter(x=>x.type==='assertion').map(x=>[x.description,true]));
 const evidence=[];
 for(const [i,a] of result.attachments.entries()){
   const ext=a.contentType==='image/png'?'png':a.contentType==='application/json'?'json':'zip';
   const name=`${id}-${i}.${ext}`,dest=path.join(process.env.QA_OUTPUT,name);
   if(a.body)fs.writeFileSync(dest,a.body);else if(a.path)fs.copyFileSync(a.path,dest);else continue;
   evidence.push({kind:a.name==='screenshot'?'screenshot':a.name==='server-verification'?'server-verification':'trace',path:name});
 }
 const log=`${id}.log`;fs.writeFileSync(path.join(process.env.QA_OUTPUT,log),JSON.stringify({title:test.title,status:result.status,errors:result.errors,duration:result.duration,assertions},null,2));evidence.push({kind:'execution-log',path:log});
 this.checks.push({id,kind:'browser',coverage_gaps:test.annotations.filter(x=>x.type==='coverage-gap').map(x=>x.description),status:toolBlocked?'blocked':result.status==='passed'?(result.retry>0?'flaky':test.annotations.some(x=>x.type==='coverage-gap')?'blocked':'passed'):result.status==='skipped'?'skipped':'failed',exit_code:result.status==='passed'?0:1,assertions,evidence});
 }
 onEnd(result){
 const current=JSON.parse(cp.execFileSync(process.env.QA_PYTHON,[path.resolve(__dirname,'../../.agents/quality/scripts/source_identity.py')],{cwd:path.resolve(__dirname,'../..'),encoding:'utf8'}));
 const drift=current.source_fingerprint!==this.identity.source_fingerprint;
 fs.writeFileSync(process.env.QA_OUTPUT+'/report.json',JSON.stringify({run:{...this.identity,kind:'website-acceptance',environment:'local-isolated',runner_status:result.status},checks:this.checks,findings:drift?[{id:'source-drift',blocking:true,status:'open'}]:[]},null,2));
 }
}
module.exports=Reporter;
