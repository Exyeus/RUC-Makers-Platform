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




