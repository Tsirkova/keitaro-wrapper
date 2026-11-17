const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
let data = {offers:[],campaigns:[]}, current = null, drafts = new Map();
let loadVersion = 0, saving = false;

const active = item => !item.state || item.state === 'active';
const offerOptions = items => items.filter(active).map(item => `<option value="${esc(item.id+' · '+item.name)}"></option>`).join('');
const offerId = value => Number(value.split(' · ')[0]);
function newDraft(stream) {
  const offers = (stream.offers || []).filter(offer => offer.state !== 'disabled');
  const original = offers.map(offer => offer.offer_id);
  return {original, ids:[...original], originalShares:new Map(offers.map(offer => [offer.offer_id,Number(offer.share)])), pins:new Map()};
}
function setSaving(value) {
  saving = value;
  document.querySelectorAll('#campaign-detail button, #campaign-detail input, #campaign-select, #reload').forEach(control => {control.disabled = value;});
}

async function api(url, options={}) {
  const response = await fetch(url,{headers:{'Content-Type':'application/json'},...options});
  const result = await response.json();
  if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
function notice(id,message,error=false) { $(id).innerHTML=`<div class="notice ${error?'error':''}">${esc(message)}</div>`; }
function options(id,items,label,placeholder) {
  $(id).innerHTML=`<option value="">${esc(placeholder)}</option>`+items.filter(x=>x.state!=='deleted').map(x=>`<option value="${x.id}">${esc(label(x))} · #${x.id}</option>`).join('');
}
function equalIds(a,b) { return a.length===b.length && a.every((id,i)=>id===b[i]); }
function percentages(draft) {
  if(!draft.ids.length)return new Map();
  if(equalIds(draft.original,draft.ids))return new Map(draft.originalShares);
  const pinned=draft.ids.filter(id=>draft.pins.has(id));
  const pinnedTotal=pinned.reduce((sum,id)=>sum+draft.pins.get(id),0);
  const free=draft.ids.filter(id=>!draft.pins.has(id));
  const left=100-pinnedTotal;
  if(left<free.length || (!free.length && left!==0))return null;
  const base=free.length?Math.floor(left/free.length):0,extra=free.length?left%free.length:0;
  const result=new Map(pinned.map(id=>[id,draft.pins.get(id)]));
  free.forEach((id,i)=>result.set(id,base+(i<extra?1:0)));
  return result;
}
async function bootstrap(selectedId) {
  try {
    data=await api('/api/bootstrap');
    $('name').placeholder=data.configured_prefix+'campaign1';
    $('offer-options').innerHTML=offerOptions(data.offers);
    options('domain_id',data.domains.filter(active),x=>x.name||x.domain,'Выберите домен');
    options('group_id',data.groups,x=>x.name,'Выберите группу');
    options('traffic_source_id',data.sources.filter(active),x=>x.name,'Выберите источник');
    options('campaign-select',data.campaigns,x=>x.name,'Выберите кампанию');
    if(selectedId){$('campaign-select').value=String(selectedId);await loadCampaign();}
  } catch(error){notice('create-message',error.message,true);}
}
async function loadCampaign() {
  const version=++loadVersion;
  const campaignId=Number($('campaign-select').value);
  $('campaign-detail').innerHTML='';current=null;drafts=new Map();
  if(!campaignId)return;
  try {
    const result=await api(`/api/campaigns/${campaignId}`);
    if(version!==loadVersion)return;
    current=result;
    for(const stream of current.streams||[]){
      if(stream.schema!=='landings')continue;
      drafts.set(stream.id,newDraft(stream));
    }
    renderCampaign();
  } catch(error){if(version===loadVersion)notice('edit-message',error.message,true);}
}
function renderCampaign() {
  if(!current)return;
  $('campaign-detail').innerHTML=`<h3>${esc(current.campaign.name)}</h3><p class="muted">ID кампании: ${current.campaign.id} · ${(current.streams||[]).length} поток(а)</p>`+
    (current.streams||[]).map(stream=>{
      const draft=drafts.get(stream.id);
      if(!draft)return `<div class="stream"><div class="stream-head"><b>${esc(stream.name||'Поток')} · #${stream.id}</b><span class="pill">${esc(stream.type||'')}</span></div><p class="muted">${esc(stream.schema||'')} · ${esc(stream.action_payload||'')} ${stream.filters?.length?'· фильтр: '+esc(stream.filters.map(x=>x.name).join(', ')):''}</p></div>`;
      const changed=!equalIds(draft.original,draft.ids), shares=percentages(draft);
      if(!shares)return `<div class="stream"><b>${esc(stream.name||'Поток')}</b><p class="error">Недостаточно свободной доли: снимите закрепление одного из офферов.</p></div>`;
      const allIds=[...draft.original,...draft.ids.filter(id=>!draft.original.includes(id))];
      const rows=allIds.map(id=>{
        const offer=data.offers.find(x=>x.id===id),removed=!draft.ids.includes(id);
        return `<div class="offer ${removed?'removed':''}"><span>${esc(offer?.name||'Оффер')} <small>#${id} · ${shares.get(id)||0}%${removed?' · удалён из черновика':''}</small></span><div class="offer-actions">${removed?'':`<button class="secondary" data-pin="${id}" data-stream="${stream.id}">${draft.pins.has(id)?'Открепить':'Закрепить долю'}</button>`}<button class="${removed?'secondary':'danger'}" data-${removed?'restore':'remove'}="${id}" data-stream="${stream.id}">${removed?'Вернуть':'Удалить'}</button></div></div>`;
      }).join('');
      const available=data.offers.filter(x=>active(x)&&!allIds.includes(x.id));
      return `<div class="stream ${changed?'draft':''}"><div class="stream-head"><b>${esc(stream.name||'Поток')} · #${stream.id}</b><span class="pill">${esc(stream.type||'')}</span></div><div class="summary"><div>Активных офферов: <b>${draft.ids.length}</b></div><div>Сумма долей: <b>${[...shares.values()].reduce((a,b)=>a+b,0)}%</b></div></div>${changed?`<p class="muted">Изменения пока не отправлены в Keitaro.</p><div class="row draft-actions"><button data-save="${stream.id}">Отправить в Keitaro</button><button class="secondary" data-cancel="${stream.id}">Отменить</button></div>`:''}<div class="offers">${rows}</div><div class="row"><input aria-label="Добавить оффер" id="add-${stream.id}" list="offers-${stream.id}" placeholder="Название или ID" autocomplete="off"><datalist id="offers-${stream.id}">${offerOptions(available)}</datalist><button data-add="${stream.id}">Добавить</button></div></div>`;
    }).join('');
}

$('create-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try {
    const body=Object.fromEntries(new FormData(event.target));
    body.offer_id=offerId(body.offer_id);
    for(const key of ['domain_id','group_id','traffic_source_id'])body[key]=Number(body[key]);
    const result=await api('/api/campaigns',{method:'POST',body:JSON.stringify(body)});
    notice('create-message',`Создана кампания #${result.campaign.id} с двумя потоками.`);
    await bootstrap(result.campaign.id);
  } catch(error){notice('create-message',error.message,true);} finally {button.disabled=false;}
});
$('campaign-select').addEventListener('change',loadCampaign);
$('reload').addEventListener('click',()=>bootstrap(Number($('campaign-select').value)));
$('campaign-detail').addEventListener('click',async event=>{
  const button=event.target.closest('button[data-add],button[data-remove],button[data-restore],button[data-save],button[data-cancel],button[data-pin]');
  if(!button||saving)return;
  const streamId=Number(button.dataset.stream||button.dataset.add||button.dataset.save||button.dataset.cancel),draft=drafts.get(streamId);
  if(!draft)return;
  if(button.dataset.add){
    const selectedId=offerId($(`add-${streamId}`).value);
    if(!data.offers.some(item=>item.id===selectedId&&active(item))||draft.ids.includes(selectedId))return notice('edit-message','Выберите доступный оффер.',true);
    draft.ids.push(selectedId);
    if(!percentages(draft)){draft.ids.pop();return notice('edit-message','Снимите закрепление доли, чтобы добавить оффер.',true);}
    renderCampaign();
  } else if(button.dataset.remove){
    if(draft.ids.length===1)return notice('edit-message','В потоке должен остаться хотя бы один оффер.',true);
    const old=[...draft.ids],removedId=Number(button.dataset.remove),pin=draft.pins.get(removedId);
    draft.ids=draft.ids.filter(id=>id!==removedId);draft.pins.delete(removedId);
    if(!percentages(draft)){draft.ids=old;if(pin!==undefined)draft.pins.set(removedId,pin);return notice('edit-message','Снимите закрепление доли перед удалением.',true);}
    renderCampaign();
  } else if(button.dataset.restore){
    const offerId=Number(button.dataset.restore),originalIndex=draft.original.indexOf(offerId);
    const next=draft.ids.findIndex(id=>draft.original.indexOf(id)===-1||draft.original.indexOf(id)>originalIndex);
    const insertAt=next===-1?draft.ids.length:next;
    draft.ids.splice(insertAt,0,offerId);
    if(!percentages(draft)){draft.ids.splice(insertAt,1);return notice('edit-message','Снимите закрепление доли перед восстановлением.',true);}
    renderCampaign();
  } else if(button.dataset.pin){
    const offerId=Number(button.dataset.pin);
    if(draft.pins.has(offerId))draft.pins.delete(offerId);
    else draft.pins.set(offerId,percentages(draft).get(offerId));
    renderCampaign();
  } else if(button.dataset.cancel){draft.ids=[...draft.original];draft.pins=new Map();renderCampaign();}
  else if(button.dataset.save){
    const campaignId=current.campaign.id;
    setSaving(true);
    try {
      const shares=percentages(draft);
      const offers=draft.ids.map(id=>({offer_id:id,share:shares.get(id)}));
      const expected_offers=draft.original.map(id=>({offer_id:id,share:draft.originalShares.get(id)}));
      const stream=await api(`/api/campaigns/${campaignId}/streams/${streamId}/offers`,{method:'PUT',body:JSON.stringify({offers,expected_offers})});
      if(current?.campaign.id===campaignId){
        current.streams=current.streams.map(item=>item.id===streamId?stream:item);
        drafts.set(streamId,newDraft(stream));
        renderCampaign();
        notice('edit-message','Изменения сохранены в Keitaro.');
      }
    } catch(error){notice('edit-message',error.message,true);} finally {setSaving(false);}
  }
});
bootstrap();
