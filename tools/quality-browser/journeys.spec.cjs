const { test, expect } = require('@playwright/test');
const fs=require('fs'), path=require('path'), cp=require('child_process');
const out=process.env.QA_OUTPUT;
async function context(browser,role){return browser.newContext({storageState:path.join(out,role+'.json')});}
async function proof(info,name){info.annotations.push({type:'assertion',description:name});}
async function shot(page,info){await info.attach('screenshot',{body:await page.screenshot({fullPage:true}),contentType:'image/png'});}
async function verify(info,mode){const data=cp.execFileSync(process.env.QA_PYTHON,[path.join(__dirname,'verify.py'),mode],{encoding:'utf8'});const parsed=JSON.parse(data.trim().split('\n').at(-1));await info.attach('server-verification',{body:JSON.stringify(parsed),contentType:'application/json'});return parsed;}
test.describe.configure({mode:'serial'});
test('TEAM-01 discover and contact a synthetic partner', async({browser},info)=>{
 const c=await context(browser,'member'),p=await c.newPage();
 await p.goto('/hackathon/quality/');
 await p.getByRole('link',{name:'参与队伍',exact:true}).first().click();
 await p.getByRole('link',{name:'匹配推荐与组队进度',exact:true}).click();
 await expect(p).toHaveURL(/market/); await proof(info,'entry_discovered_without_route_hints');
 const card=p.locator('#people article').filter({has:p.getByRole('heading',{name:'合成队长'})});
 await expect(card).toContainText('Python'); info.annotations.push({type:'coverage-gap',description:'Profile text visible; independent stranger comprehension not exercised.'});
 await card.getByText('表达合作意向',{exact:true}).click();
 await card.locator('textarea[name=message]').fill('合成合作意向：一起实现 Python 测试');
 await card.getByRole('button',{name:'发送意向'}).click();
 const cc=await context(browser,'captain'),recipient=await cc.newPage();
 await recipient.goto('/hackathon/quality/market/');
 await expect(recipient.locator('#progress')).toContainText('合成合作意向');
 await recipient.getByRole('button',{name:'同意交流',exact:true}).click();
 await proof(info,'contact_or_request_reaches_intended_recipient'); await shot(recipient,info);await c.close();await cc.close();
});
test('TEAM-02 application and both persisted rosters',async({browser},info)=>{
 const c=await context(browser,'member'),cc=await context(browser,'captain'),p=await c.newPage(),captain=await cc.newPage();
 const fixture=JSON.parse(fs.readFileSync(path.join(out,'fixture.json')));expect(fixture.captain).not.toBe(fixture.member);expect(JSON.parse(fs.readFileSync(path.join(out,'captain.json'))).cookies[0].value).not.toBe(JSON.parse(fs.readFileSync(path.join(out,'member.json'))).cookies[0].value); await proof(info,'distinct_accounts_used');
 await p.goto('/hackathon/quality/teams/discover/');
 const card=p.locator('article').filter({has:p.getByRole('heading',{name:'合成验收队',exact:true})});
 await card.getByText('申请加入',{exact:true}).click(); await card.locator('textarea').fill('合成入队申请'); await card.getByRole('button',{name:'发送申请'}).click();
 await captain.goto('/hackathon/quality/teams/'); await captain.getByRole('button',{name:'同意加入',exact:true}).click();
 await proof(info,'agreed_team_transition_completed');
 for(const page of [p,captain]){await page.goto('/hackathon/quality/teams/');await expect(page.locator('.member-list')).toContainText('合成伙伴');await expect(page.locator('.member-list')).toContainText('合成队长');}
 await verify(info,'team');await proof(info,'both_accounts_observe_same_persisted_state');await shot(p,info);await c.close();await cc.close();
});
test('SUBMIT-01 transfer failure retains file and resumes',async({browser},info)=>{
 const c=await context(browser,'captain'),p=await c.newPage();await p.goto('/hackathon/quality/project/');
 await p.evaluate(() => {window.qaStatuses=[]; const el=document.querySelector('[data-upload-status]');new MutationObserver(()=>window.qaStatuses.push(el.textContent)).observe(el,{childList:true,subtree:true,characterData:true});});
 let fault=true;await p.route('**/finish/',route=>{if(fault){fault=false;return route.abort('connectionreset');}return route.continue();});
 const bytes=Buffer.alloc(17*1048576,97);Buffer.from('%PDF-1.7\n').copy(bytes);
 await p.locator('[data-upload-form] select[name=kind]').selectOption('slides');await p.locator('[data-upload-form] input[type=file]').setInputFiles({name:'synthetic.pdf',mimeType:'application/pdf',buffer:bytes});
 const cdp=await c.newCDPSession(p);await cdp.send('Network.enable');await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:50,downloadThroughput:-1,uploadThroughput:1048576});
 await p.locator('[data-upload-form] [type=submit]').click();await expect(p.locator('[data-upload-progress]')).toBeVisible();await proof(info,'pending_visible');
 await expect(p.locator('[data-upload-speed]')).toContainText(/\d/);await proof(info,'measured_progress_or_truthful_unknown_total');
 await p.locator('[data-pause-upload]').click();await expect(p.locator('[data-upload-status]')).toContainText('已暂停');
 expect(await p.locator('[data-upload-form] input[type=file]').evaluate(e=>e.files[0].name)).toBe('synthetic.pdf');
 await expect(p.locator('[data-upload-form] [type=submit]')).toBeEnabled();
 // Inject one failed network acknowledgement; successful storage remains real.
 await shot(p,info);
 await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});
 await p.locator('[data-upload-form] [type=submit]').click();await expect(p.locator('[data-upload-status]')).toContainText('已保留服务器凭证',{timeout:30000});
 expect(await p.locator('[data-upload-form] input[type=file]').evaluate(e=>e.files[0].name)).toBe('synthetic.pdf');await expect(p.locator('[data-pause-upload]')).toBeVisible();await proof(info,'failure_keeps_input_and_offers_recovery');
 await expect(p.locator('[data-upload-status]')).toContainText('材料已保存',{timeout:90000});
 expect((await p.evaluate(()=>window.qaStatuses)).some(s=>s.includes('服务器正在校验'))).toBeTruthy();
 await verify(info,'upload');await proof(info,'transfer_and_server_processing_distinguished');await c.close();
});
test('SUBMIT-02 formal receipt survives reload with stored materials',async({browser},info)=>{
 const c=await context(browser,'captain'),p=await c.newPage();await p.goto('/hackathon/quality/project/');
 await expect(p.locator('[data-project-badge]')).toContainText('草稿');await proof(info,'draft_and_submission_distinguished');
 await p.locator('[data-upload-form] select[name=kind]').selectOption('poster');
 const png=fs.readFileSync(path.join(out,'poster.png'));
 await p.locator('[data-upload-form] input[type=file]').setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:png});await p.locator('[data-upload-form] [type=submit]').click();await expect(p.locator('[data-upload-status]')).toContainText('材料已保存');
 await p.locator('[data-submit-project]').click();await expect(p.locator('[data-submission-state]')).toContainText('当前版本已正式提交');const receiptText=await p.locator('[data-receipt-id]').textContent();expect(receiptText).toMatch(/^[0-9a-f-]{36}$/);await proof(info,'server_confirms_receipt');
 await p.reload();await expect(p.locator('[data-submission-state]')).toContainText('当前版本已正式提交');await proof(info,'record_survives_reload');const server=await verify(info,'submission');expect(receiptText).toContain(server.receipt);await proof(info,'correct_team_and_materials_persisted');await shot(p,info);await c.close();
});





test('AUTH-01 real login and expired-session recovery',async({browser},info)=>{
 const c=await browser.newContext(),p=await c.newPage();
 await p.goto('/hackathon/quality/');
 const form=p.locator('#join form');
 await form.locator('[name=identifier]').fill('QACAPTAIN');
 await form.locator('[name=password]').fill('wrong-password');
 await form.getByRole('button',{name:'登录',exact:true}).click();
 await expect(p.locator('#join')).toContainText('学号、邮箱或密码不正确');
 await expect(p.locator('#join')).toContainText('忘记密码');await proof(info,'failure_is_actionable');
 await p.locator('#join [name=identifier]').fill('QACAPTAIN');
 await p.locator('#join [name=password]').fill('QA-local-only-2026!');
 await p.locator('#join').getByRole('button',{name:'登录',exact:true}).click();
 await expect(p.getByRole('link',{name:'我的作品',exact:true})).toBeVisible();await proof(info,'fresh_account_can_log_in');
 await p.goto('/hackathon/quality/project/');
 await expect(p.locator('[name=title]')).toHaveValue('合成验收作品');
 const cookie=(await c.cookies()).find(x=>x.name==='eventyay_session');expect(cookie).toBeTruthy();
 cp.execFileSync(process.env.QA_PYTHON,[path.join(__dirname,'expire_session.py')],{input:cookie.value});
 await p.reload();
 await expect(p).toHaveURL(/next=/);
 await p.locator('#join [name=identifier]').fill('QACAPTAIN');
 await p.locator('#join [name=password]').fill('QA-local-only-2026!');
 await p.locator('#join').getByRole('button',{name:'登录',exact:true}).click();
 await expect(p).toHaveURL(/project/);await expect(p.locator('[name=title]')).toHaveValue('合成验收作品');
 await proof(info,'expired_session_preserves_recoverable_work');await shot(p,info);await c.close();
});
test('OPS-01 organizer checks receipt and export',async({browser},info)=>{
 const c=await context(browser,'organizer'),p=await c.newPage();
 await p.goto('/hackathon/quality/manage/');
 await expect(p.locator('body')).toContainText('支持中心');
 await expect(p.getByRole('link',{name:'去处理'}).last()).toBeVisible();
 await proof(info,'fallback_contact_or_channel_is_visible');
 await p.getByRole('link',{name:'全部作品'}).click();
 const row=p.locator('tr').filter({hasText:'合成验收作品'});
 const server=await verify(info,'submission');
 await expect(row).toContainText(server.receipt);await proof(info,'organizer_finds_submission_receipt');await shot(p,info);
 await p.goto('/hackathon/quality/manage/');
 const [download]=await Promise.all([p.waitForEvent('download'),p.getByRole('link',{name:'作品与提交记录 CSV'}).click()]);
 const csv=fs.readFileSync(await download.path(),'utf8');
 const rows=csv.trim().split(/\r?\n/);expect(rows).toHaveLength(2);
 expect(rows[1]).toContain(server.project_code);expect(rows[1]).toContain(server.team_code);
 expect(rows[1]).toContain('合成验收作品');expect(rows[1]).toContain('合成验收队');expect(rows[1]).toContain(server.state_display);
 await proof(info,'export_matches_confirmed_records');await c.close();
});

test('SUBMIT-03 duplicate submission, lost response and server deadline',async({browser},info)=>{
 const c=await context(browser,'captain'),p=await c.newPage(); await p.goto('/hackathon/quality/project/');
 const body=await p.locator('#project-information-form').evaluate(form=>{const data=new FormData(form);data.set('action','submit');return new URLSearchParams(data).toString();});
 let intercepted=false;
 await p.route('**/hackathon/quality/project/',async route=>{if(!intercepted && route.request().method()==='POST'){intercepted=true;await route.fetch();await route.abort('connectionreset');}else await route.continue();});
 const send=()=>p.evaluate(async body=>{try{const r=await fetch('/hackathon/quality/project/',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});return r.status}catch(e){return 'network-error'}},body);
 expect(await send()).toBe('network-error');expect(intercepted).toBe(true);
 const first=await verify(info,'state');expect(await send()).toBe(200);
 const second=await verify(info,'state');expect(second.receipt).toBe(first.receipt);expect(second.revision).toBe(first.revision);await proof(info,'retry_or_double_click_has_no_duplicate_submission');
 await p.reload();await expect(p.locator('[data-receipt-id]')).toHaveText(first.receipt);await proof(info,'ambiguous_network_result_reconciled');
 const active=await p.evaluate(async()=>{const data=new URLSearchParams({id:crypto.randomUUID(),kind:'slides',filename:'pending.pdf',size:'9'});const token=document.querySelector('[name=csrfmiddlewaretoken]').value;const r=await fetch('/hackathon/quality/project/uploads/',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRFToken':token},body:data});return {status:r.status,body:await r.json()};});expect(active.status).toBe(200);expect(active.body.state).toBe('hashing');
 await verify(info,'deadline');await p.reload();
 const response=await p.evaluate(async()=>{const data=new URLSearchParams({id:crypto.randomUUID(),kind:'slides',filename:'late.pdf',size:'9'});const token=document.querySelector('[name=csrfmiddlewaretoken]').value;return (await fetch('/hackathon/quality/project/uploads/',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRFToken':token},body:data})).status;});expect(response).toBe(400);const after=await verify(info,'state');expect(after.receipt).toBe(first.receipt);expect(after.revision).toBe(first.revision);await proof(info,'agreed_server_deadline_enforced');await shot(p,info);await c.close();
});

test('SUBMIT-04 cross-account upload and private asset isolation',async({browser},info)=>{
 const owner=await context(browser,'captain'), outsider=await context(browser,'outsider'),anon=await browser.newContext();
 const data=await verify(info,'state');expect(data.upload_id).toBeTruthy();expect(data.private_asset).toBeTruthy();
 const base='/hackathon/quality/', upload=base+'project/uploads/'+data.upload_id+'/', asset=base+'assets/'+data.private_asset+'/';
 const op=await owner.newPage(),p=await outsider.newPage(),guest=await anon.newPage();await op.goto(base+'project/');await p.goto(base+'market/');await guest.goto(base);
 expect((await op.request.get(upload)).status()).toBe(200);expect((await op.request.get(asset)).status()).toBe(200);
 expect((await p.request.get(upload)).status()).toBe(404);const csrf=(await owner.cookies()).find(x=>x.name==='eventyay_csrftoken');expect(csrf).toBeTruthy();await outsider.addCookies([csrf]);const token=csrf.value;expect((await p.request.post(upload+'finish/',{headers:{'X-CSRFToken':token,'Referer':p.url()}})).status()).toBe(404);await proof(info,'other_account_cannot_read_or_continue_upload');await proof(info,'server_checks_ownership');
 expect((await p.request.get(asset)).status()).toBe(404);const publicResponse=await guest.request.get(asset,{maxRedirects:0});expect(publicResponse.status()).not.toBe(200);await proof(info,'private_materials_not_publicly_exposed');await shot(p,info);await owner.close();await outsider.close();await anon.close();
});
