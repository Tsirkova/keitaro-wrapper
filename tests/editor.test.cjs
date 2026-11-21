const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

let browser;
before(async () => {
  browser = await chromium.launch({executablePath:process.env.BROWSER_EXECUTABLE_PATH || undefined});
});
after(async () => {await browser?.close();});

async function setup(t, {slowFirst=false, rejectSave=false}={}) {
  const page = await browser.newPage();
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const writes = [];
  const stream = (id, ids) => ({id, name:'Offers', schema:'landings', type:'default',
    offers:ids.map(offer_id => ({offer_id, share:100/ids.length, state:'active'}))});
  const campaigns = {
    11:{campaign:{id:11,name:'First campaign'},streams:[stream(22,[1,2]),stream(23,[1])]},
    12:{campaign:{id:12,name:'Second campaign'},streams:[stream(32,[3])]},
  };
  const offers = ['Alpha','Beta','Gamma','Delta'].map((name,i)=>({id:i+1,name,state:'active'}));
  offers.push({id:5,name:'Disabled',state:'disabled'});
  await page.route('http://keitaro.test/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const json = body => route.fulfill({json:body});
    if(url.pathname === '/api/bootstrap') return json({
      configured_prefix:'test_surname_', offers,
      domains:[{id:1,name:'example.com'}], groups:[{id:1,name:'FORTESTS'}],
      sources:[{id:1,name:'FORTESTS'}], campaigns:Object.values(campaigns).map(c=>c.campaign),
    });
    if(request.method() === 'PUT') {
      if(rejectSave) return route.fulfill({status:409,json:{detail:'Reload the campaign'}});
      const [,campaignId,streamId] = url.pathname.match(/campaigns\/(\d+)\/streams\/(\d+)/);
      const body = request.postDataJSON();
      writes.push(body);
      const target = campaigns[campaignId].streams.find(s=>s.id===Number(streamId));
      target.offers = body.offers.map(o=>({...o,state:'active'}));
      return json(target);
    }
    if(url.pathname === '/api/campaigns' && request.method() === 'POST') {
      writes.push(request.postDataJSON());
      return json(campaigns[11]);
    }
    if(url.pathname.startsWith('/api/campaigns/')) {
      const id = url.pathname.split('/').pop();
      if(slowFirst && id==='11') await new Promise(resolve=>setTimeout(resolve,200));
      return json(campaigns[id]);
    }
    const file = url.pathname === '/' ? 'index.html' : 'app.js';
    return route.fulfill({body:fs.readFileSync(path.join(__dirname,'../app/static',file)),
      contentType:file.endsWith('html')?'text/html':'application/javascript'});
  });
  await page.goto('http://keitaro.test/');
  await page.locator('#campaign-select option[value="11"]').waitFor({state:'attached'});
  const select = async id => {
    await page.selectOption('#campaign-select', String(id));
    await page.locator(`[data-add="${id===11?22:32}"]`).waitFor();
  };
  const add = async (id, streamId=22) => {
    await page.fill(`#add-${streamId}`, String(id));
    await page.click(`[data-add="${streamId}"]`);
  };
  const shares = async (streamId=22) => page.locator('.stream').filter({has:page.locator(`[data-add="${streamId}"]`)})
    .locator('.offer:not(.removed) small').evaluateAll(nodes=>nodes.map(n=>Number(n.textContent.match(/(\d+)%/)[1])));
  return {page,writes,select,add,shares};
}

test('add, pin, remove, restore and cancel keep the demonstrated percentages', async t => {
  const {page,writes,select,add,shares} = await setup(t);
  await select(11);
  await add(3);
  assert.deepEqual(await shares(),[34,33,33]);
  await add(4);
  assert.deepEqual(await shares(),[25,25,25,25]);
  await page.click('[data-pin="1"][data-stream="22"]');
  await page.click('[data-remove="2"][data-stream="22"]');
  assert.deepEqual(await shares(),[25,38,37]);
  assert.match(await page.locator('.offer.removed').innerText(), /0%/);
  await page.click('[data-restore="2"][data-stream="22"]');
  assert.deepEqual(await shares(),[25,25,25,25]);
  await page.click('[data-cancel="22"]');
  assert.deepEqual(await shares(),[50,50]);
  assert.equal(writes.length,0);
});

test('saving one stream preserves the draft in another stream', async t => {
  const {page,writes,select,add,shares} = await setup(t);
  await select(11);
  await add(3);
  await add(2,23);
  await page.click('[data-save="22"]');
  await page.getByText('Изменения сохранены в Keitaro.').waitFor();
  assert.deepEqual(writes[0].offers.map(o=>o.share),[34,33,33]);
  assert.deepEqual(writes[0].expected_offers,[{offer_id:1,share:50},{offer_id:2,share:50}]);
  assert.deepEqual(await shares(23),[50,50]);
  assert.equal(await page.locator('[data-save="23"]').count(),1);
});

test('a slow response cannot replace the most recently selected campaign', async t => {
  const {page,select} = await setup(t,{slowFirst:true});
  await page.selectOption('#campaign-select','11');
  await select(12);
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#campaign-detail h3').innerText(),'Second campaign');
});

test('failed save retains the draft and allows retry', async t => {
  const {page,select,add,shares} = await setup(t,{rejectSave:true});
  await select(11);
  await add(3);
  await page.click('[data-save="22"]');
  await page.getByText('Reload the campaign').waitFor();
  assert.deepEqual(await shares(),[34,33,33]);
  assert.equal(await page.locator('[data-save="22"]').isEnabled(),true);
});

test('last offer and unavailable offers cannot be removed or added', async t => {
  const {page,writes,select,add,shares} = await setup(t);
  await select(12);
  await page.click('[data-remove="3"]');
  assert.deepEqual(await shares(32),[100]);
  await add(5,32);
  assert.deepEqual(await shares(32),[100]);
  assert.equal(writes.length,0);
});

test('autocomplete selections send the numeric offer ID', async t => {
  const {page,writes} = await setup(t);
  assert.equal(await page.locator('#offer-options option').count(),4);
  await page.fill('#name','test_surname_demo');
  await page.fill('#country','AU');
  await page.fill('#offer_id','3 · Gamma');
  for(const field of ['domain_id','group_id','traffic_source_id']) await page.selectOption('#'+field,'1');
  await page.click('#create-form button');
  await page.getByText('Создана кампания #11 с двумя потоками.').waitFor();
  assert.equal(writes[0].offer_id,3);
});

test('manual share input rebalances the other offers and can be saved without adding offers', async t => {
  const {page,writes,select,shares} = await setup(t);
  await select(11);
  assert.equal(await page.locator('[data-save="22"]').count(),0);
  await page.fill('[data-share="1"][data-stream="22"]','70');
  await page.locator('[data-share="1"][data-stream="22"]').dispatchEvent('change');
  assert.deepEqual(await shares(),[70,30]);
  await page.click('[data-save="22"]');
  await page.getByText('Изменения сохранены в Keitaro.').waitFor();
  assert.deepEqual(writes[0].offers,[{offer_id:1,share:70},{offer_id:2,share:30}]);
});

test('pinning without editing keeps shares; impossible share is rejected', async t => {
  const {page,select,add,shares} = await setup(t);
  await select(11);
  await page.click('[data-pin="1"][data-stream="22"]');
  assert.deepEqual(await shares(),[50,50]);
  assert.equal(await page.locator('[data-save="22"]').count(),0);
  await add(3);
  await page.fill('[data-share="2"][data-stream="22"]','100');
  await page.locator('[data-share="2"][data-stream="22"]').dispatchEvent('change');
  await page.getByText('Такая доля не помещается', {exact:false}).waitFor();
  assert.deepEqual(await shares(),[50,25,25]);
});

test('geo accepts a comma separated list', async t => {
  const {page,writes} = await setup(t);
  await page.fill('#name','test_surname_demo');
  await page.fill('#country','MX,AU,RO');
  await page.fill('#offer_id','3 · Gamma');
  for(const field of ['domain_id','group_id','traffic_source_id']) await page.selectOption('#'+field,'1');
  await page.click('#create-form button');
  await page.getByText('Создана кампания #11 с двумя потоками.').waitFor();
  assert.equal(writes[0].country,'MX,AU,RO');
});
