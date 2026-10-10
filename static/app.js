'use strict';
const $ = id => document.getElementById(id);
const token = new URLSearchParams(location.hash.slice(1)).get('token');
const titles = {all:'全部未删除',pending:'待筛选',keep:'已保留',deleted:'已删除 · 可恢复',reject:'淘汰',duplicates:'完全重复',errors:'预览异常'};
let current=null, upcoming=null, view='pending', gridMode=false, currentReady=false;
let state=null, initialized=false, renderVersion=0, contextEpoch=0;
let buffer=[], atEnd=false, pendingFill=null, handled=new Set(), stripRows=[];
let gridAnchors=[0], gridIndex=0, gridRows=[];
let queue=Promise.resolve(), pollBusy=false, toastTimer, cursorTimer;
let detailVersion=0, detailPhoto=null, detailPreviousFocus=null;
let lastDeleted=null;
const thumbNodes=new Map(), warmImages=new Map();
let warmQueue=[], warming=0;

function toast(message){
  $('toast').textContent=message;$('toast').hidden=false;
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('toast').hidden=true;},3500);
}
async function api(path,body){
  const response=await fetch('/api/'+path,{headers:{'X-Photo-Token':token||'','Content-Type':'application/json'},
    method:body===undefined?'GET':'POST',body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){
    let data;try{data=await response.json();}catch{data={detail:response.status===403?'请使用终端打印的完整本机地址打开。':response.statusText};}
    throw new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
  }
  return response.json();
}
function enqueue(action){queue=queue.then(action).catch(error=>toast(error.message));return queue;}
function imageUrl(photo,edge=1600){return '/api/image/'+photo.id+'?edge='+edge+'&v='+photo.mtime+'&token='+encodeURIComponent(token||'');}
function eligible(photo){
  if(handled.has(photo.id))return false;
  if(view==='pending')return photo.status===0;
  if(view==='keep')return photo.status===1;
  if(view==='reject')return photo.status===-1;
  if(view==='deleted')return photo.deleted;
  return true;
}
function invalidate(){contextEpoch++;buffer=[];atEnd=false;pendingFill=null;handled.clear();}
async function refill(photo){
  if(pendingFill)return pendingFill;
  const epoch=contextEpoch, requestedView=view;
  const task=(async()=>{
    const context=await api('context/'+photo.id+'?view='+requestedView);
    if(epoch!==contextEpoch)return;
    const merged=new Map(buffer.map(p=>[p.id,p]));
    for(const p of [...context.before,context.current,...context.after])if(eligible(p))merged.set(p.id,p);
    const rows=[...merged.values()].filter(eligible).sort((a,b)=>a.id-b.id);
    const center=current?current.id:photo.id;
    buffer=[...rows.filter(p=>p.id<center).slice(-4),...rows.filter(p=>p.id>=center).slice(0,41)];
    atEnd=context.after.length<40;
  })();
  pendingFill=task;
  try{await task;}finally{if(pendingFill===task)pendingFill=null;}
}
function neighbors(id,direction='next'){
  const rows=buffer.filter(p=>eligible(p)&&(direction==='next'?p.id>id:p.id<id));
  return direction==='next'?rows[0]:rows.at(-1);
}
function updateCounts(){
  if(!state)return;
  for(const [key,value] of Object.entries(state.counts)){const node=$('n-'+key);if(node)node.textContent=value.toLocaleString();}
}
function adjustCounts(photo,deleted=false,newStatus=photo.status){
  if(!state)return;
  const key={0:'pending',1:'keep','-1':'reject'};
  if(deleted){state.counts.all--;state.counts[key[photo.status]]--;state.counts.deleted++;}
  else if(photo.status!==newStatus){state.counts[key[photo.status]]--;state.counts[key[newStatus]]++;}
  updateCounts();
}
async function refreshState(autoStart=true){
  if(pollBusy)return;
  pollBusy=true;
  try{
    const scanning=state?.job.running&&['扫描文件夹','移动保留照片'].includes(state.job.kind);
    state=await api('state');updateCounts();
    lastDeleted=state.last_deleted;renderLastDelete();renderSaveState();
    if(state.root){$('folder').value=state.root;$('rootHelp').textContent='↓ 删除 · ↑ 保留 · ← 恢复最近删除。';}
    $('scan').disabled=state.job.running;$('dedup').disabled=state.job.running||!state.counts.all;
    const j=state.job;
    $('job').hidden=!j.running&&!j.message;
    $('jobText').textContent=j.kind+' · '+j.done.toLocaleString()+(j.total?' / '+j.total.toLocaleString():'')+' 张 · '+(j.running?'处理中':j.message);
    if(j.total){$('progress').max=j.total;$('progress').value=j.done;}
    else if(j.running)$('progress').removeAttribute('value');
    else{$('progress').value=1;$('progress').max=1;}
    $('warnings').hidden=!j.errors.length;$('warningText').textContent=j.errors.join('\n');
    if(initialized&&scanning&&!j.running){
      enqueue(async()=>{invalidate();if(gridMode)return loadGrid();if(current){try{const p=await api('photo/'+current.id);if(eligible(p))return show(p);}catch{}}return first();});
    }else if(autoStart&&initialized&&!current&&!gridMode&&state.counts.pending){
      enqueue(()=>{if(!current)return first();});
    }
  }catch(error){toast(error.message);}finally{pollBusy=false;}
}
function rememberCursor(id){
  try{localStorage.setItem('photo-sieve-cursor',JSON.stringify({root:state?.root,id}));}catch{}
  clearTimeout(cursorTimer);cursorTimer=setTimeout(()=>api('cursor',{id}).catch(()=>{}),200);
}
window.addEventListener('pagehide',()=>{
  if(current&&token)navigator.sendBeacon('/api/cursor?token='+encodeURIComponent(token),new Blob([JSON.stringify({id:current.id})],{type:'application/json'}));
});
async function first(){
  const rows=await api('photos?view='+view+'&limit=1');
  if(rows.length)await show(rows[0]);else clearViewer();
}
function clearViewer(){
  current=null;upcoming=null;currentReady=false;renderVersion++;rememberCursor(0);
  $('viewer').hidden=true;$('grid').hidden=true;$('empty').hidden=false;
  $('empty').querySelector('h2').textContent=state&&state.counts.all?'这个队列已筛选完':'当前没有照片';
  $('empty').querySelector('p').textContent='已保留的照片可在左侧“已保留”查看；按 ← 恢复最近删除，Ctrl+Z 撤回最近操作。';
}
function setPreview(element,photo,revision,onReady,onError){
  const url=imageUrl(photo);
  element.onload=()=>{if(revision!==renderVersion)return;element.hidden=false;onReady();};
  element.onerror=()=>{if(revision!==renderVersion)return;element.hidden=true;onError();};
  if(element.getAttribute('src')!==url){element.hidden=true;element.src=url;}
  if(element.complete&&element.naturalWidth){element.hidden=false;onReady();}
}
async function show(photo){
  closeDetail();current=photo;currentReady=false;const revision=++renderVersion;
  gridMode=false;$('grid').hidden=true;$('viewer').hidden=false;$('empty').hidden=true;
  $('gridToggle').textContent='缩略图网格';$('filename').textContent=photo.name;
  $('filepath').textContent=photo.relative;$('filepath').title=photo.path;updateBadge();
  $('loading').hidden=false;$('imageError').hidden=true;
  setPreview($('photo'),photo,revision,()=>{currentReady=true;$('loading').hidden=true;},()=>{
    currentReady=true;$('loading').hidden=true;$('imageError').hidden=false;
    $('imageError').textContent='无法读取预览，请检查格式或硬盘连接。';
    api('photo/'+photo.id).then(p=>{if(revision===renderVersion&&p.error)$('imageError').textContent=p.error;}).catch(()=>{});
  });
  rememberCursor(photo.id);
  const ahead=buffer.filter(p=>p.id>photo.id&&eligible(p));
  if(!buffer.length||(!ahead.length&&!atEnd))await refill(photo);
  if(revision!==renderVersion)return;
  updateNeighbors(revision);
  if(buffer.filter(p=>p.id>photo.id&&eligible(p)).length<10&&!atEnd){
    refill(photo).then(()=>{if(revision===renderVersion)updateNeighbors(revision);}).catch(error=>toast(error.message));
  }
}
function updateNeighbors(revision){
  if(!current||revision!==renderVersion)return;
  const after=buffer.filter(p=>p.id>current.id&&eligible(p));upcoming=after[0]||null;
  if(upcoming){
    $('nextLabel').textContent='下一张 · '+upcoming.name;$('nextLabel').title=upcoming.path;
    $('nextEmpty').hidden=false;$('nextEmpty').textContent='正在读取下一张…';
    setPreview($('nextPhoto'),upcoming,revision,()=>{$('nextEmpty').hidden=true;},()=>{$('nextEmpty').hidden=false;$('nextEmpty').textContent='下一张无法预览';});
  }else{
    $('nextPhoto').hidden=true;$('nextLabel').textContent='下一张';$('nextEmpty').hidden=false;$('nextEmpty').textContent='已到队列末尾';
  }
  const before=buffer.filter(p=>p.id<current.id&&eligible(p)).slice(-4);
  // Kept photos stay available in the dedicated view, but never in the lower filmstrip.
  stripRows=[...before,current,...after.slice(0,10)].filter(p=>p.status!==1&&!handled.has(p.id));
  drawStrip();scheduleWarm(after.slice(1,7));
}
function updateBadge(){
  if(!current)return;
  $('badge').textContent={0:'未筛选',1:'已保留','-1':'已淘汰'}[current.status];
  $('badge').className=current.status===1?'keep':current.status===-1?'reject':'';
}
function drawStrip(){
  const fragment=document.createDocumentFragment(), visible=new Set();
  for(const photo of stripRows){
    visible.add(photo.id);let button=thumbNodes.get(photo.id);
    if(!button){
      button=document.createElement('button');button.className='thumb';
      const img=document.createElement('img');img.alt=photo.name;img.loading='lazy';img.decoding='async';img.src=imageUrl(photo,320);
      button.append(img);button.onclick=()=>enqueue(async()=>show(await api('photo/'+photo.id)));thumbNodes.set(photo.id,button);
    }
    const img=button.firstElementChild,url=imageUrl(photo,320);if(img.getAttribute('src')!==url)img.src=url;
    button.dataset.photoId=photo.id;button.title=photo.relative;button.classList.toggle('selected',current?.id===photo.id);fragment.append(button);
  }
  $('filmstrip').replaceChildren(fragment);
  for(const id of thumbNodes.keys())if(!visible.has(id))thumbNodes.delete(id);
  $('filmstrip').querySelector('.selected')?.scrollIntoView({block:'nearest',inline:'center'});
}
function scheduleWarm(photos){
  warmQueue=photos.filter(p=>!warmImages.has(imageUrl(p)));pumpWarm();
}
function pumpWarm(){
  while(warming<2&&warmQueue.length){
    const photo=warmQueue.shift(),url=imageUrl(photo);if(warmImages.has(url)||handled.has(photo.id))continue;
    const image=new Image();image.decoding='async';image.fetchPriority='low';warmImages.set(url,image);warming++;
    image.onload=image.onerror=()=>{warming--;pumpWarm();};image.src=url;
    while(warmImages.size>24)warmImages.delete(warmImages.keys().next().value);
  }
}
async function navigate(direction){
  if(!current)return first();
  let photo=neighbors(current.id,direction);
  if(!photo&&(direction==='prev'||!atEnd)){await refill(current);photo=neighbors(current.id,direction);}
  if(photo)await show(photo);else toast(direction==='next'?'已到队列末尾':'已到队列开头');
}
async function advanceFrom(id){
  let next=neighbors(id);
  if(!next&&!atEnd){const rows=await api('photos?view='+view+'&anchor='+id+'&limit=1');next=rows[0];}
  if(next)return show(next);
  const previous=neighbors(id,'prev');if(previous)return show(previous);
  await first();
}
async function mark(status){
  if(!current||gridMode)return;
  if(!currentReady){toast('请等待当前照片加载完成。');return;}
  const photo=current;await api('mark',{id:photo.id,status});adjustCounts(photo,false,status);photo.status=status;
  if(status===1){handled.add(photo.id);buffer=buffer.filter(p=>p.id!==photo.id);await advanceFrom(photo.id);}
  else{updateBadge();invalidate();await show(photo);}
}
async function deleteCurrent(){
  if(!current||gridMode)return;
  if(!currentReady){toast('请等待当前预览，看清照片后再删除。');return;}
  const photo=current;currentReady=false;
  let result;try{result=await api('trash',{id:photo.id});}catch(error){currentReady=true;throw error;}
  lastDeleted={...photo,backup:result.backup};renderLastDelete();
  adjustCounts(photo,true);handled.add(photo.id);buffer=buffer.filter(p=>p.id!==photo.id);
  await advanceFrom(photo.id);toast('已删除 '+photo.name+' · ← 恢复 · 去向见上方');
}
async function undo(onlyDelete=false){
  const result=await api(onlyDelete?'undo-delete':'undo',{});
  invalidate();view=result.status===1?'keep':'pending';updateView();
  await show(await api('photo/'+result.id));toast(result.restored?'已恢复照片原位置':'已撤回最近的标记');
  refreshState(false);
}
function parentOf(path){return path?path.replace(/[\\/][^\\/]*$/,''):'';}
function openFolder(path){return api('open-folder',{path}).catch(error=>toast(error.message));}
function renderLastDelete(){
  $('lastDelete').hidden=!lastDeleted;
  if(!lastDeleted)return;
  $('lastDeleteName').textContent='最近删除：'+lastDeleted.name;
  $('lastDeletePath').textContent='待删除位置：'+lastDeleted.backup;
  $('lastDeletePath').title='原位置：'+lastDeleted.path;
}
async function restoreSpecific(id){
  await api('restore/'+id,{});invalidate();await refreshState(false);toast('照片已恢复到原位置');
  if(view==='deleted'){await loadGrid();if(!gridRows.length&&gridIndex>0){gridIndex--;await loadGrid();}}
  else{view='pending';gridMode=false;updateView();await show(await api('photo/'+id));}
}
function renderSaveState(){
  if(!state)return;
  $('saveCount').textContent='当前有 '+state.counts.keep.toLocaleString()+' 张已保留照片。';
  $('copyKept').disabled=$('moveKept').disabled=state.job.running||!state.counts.keep;
  const j=state.job;$('saveTaskStatus').textContent=['复制保留照片','移动保留照片'].includes(j.kind)?j.kind+' · '+j.done+' / '+j.total+' · '+(j.running?'处理中':j.message):'';
  const result=state.last_export;$('saveResult').hidden=!result;
  if(!result)return;
  $('saveSummary').textContent=(result.mode==='copy'?'复制':'移动')+'：成功 '+result.done+' 张，未成功 '+result.failed+' 张，剩余 '+result.pending+' 张。';
  $('saveDestination').textContent='目标位置：'+result.destination;
  $('saveErrors').hidden=!result.errors.length;
  $('saveErrorText').textContent=result.errors.map(error=>error.original+'\n'+error.error).join('\n\n');
}
function openSave(){
  closeDetail();$('saveDialog').hidden=false;renderSaveState();
  if(!$('destination').value&&state?.last_export)$('destination').value=state.last_export.destination;
  $('chooseDestination').focus();
}
async function saveKept(mode){
  const destination=$('destination').value.trim();
  if(!destination){toast('请先选择目标文件夹或粘贴完整路径。');return;}
  $('copyKept').disabled=$('moveKept').disabled=true;
  try{await api('save-kept',{destination,mode});await refreshState(false);toast('开始'+(mode==='copy'?'复制':'移动')+'已保留照片。');}
  catch(error){renderSaveState();throw error;}
}
function updateView(){
  $('heading').textContent=titles[view];document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
}
async function selectView(next){view=next;invalidate();updateView();gridAnchors=[0];gridIndex=0;if(next==='deleted'){current=null;await loadGrid();}else if(gridMode)await loadGrid();else await first();}
async function loadGrid(){
  closeDetail();gridMode=true;renderVersion++;$('viewer').hidden=true;$('empty').hidden=true;$('grid').hidden=false;$('gridToggle').textContent='返回双图筛选';
  gridRows=await api('photos?view='+view+'&anchor='+gridAnchors[gridIndex]+'&limit=60');$('gridCards').replaceChildren();
  $('deletedHelp').hidden=view!=='deleted';
  for(const photo of gridRows){
    if(view==='deleted'){
      const card=document.createElement('article');card.className='card deleted-card';card.dataset.photoId=photo.id;
      const preview=document.createElement('button');preview.className='deleted-preview';const img=document.createElement('img');img.alt=photo.name;img.loading='lazy';img.src=imageUrl(photo,320);preview.append(img);preview.onclick=()=>openDetail(photo);
      const title=document.createElement('strong');title.textContent=photo.name;
      const original=document.createElement('div');original.className='location';const originalLabel=document.createElement('strong');originalLabel.textContent='原位置';original.append(originalLabel,document.createTextNode(photo.path));
      const location=document.createElement('div');location.className='location';const label=document.createElement('strong');label.textContent='待删除位置';location.append(label,document.createTextNode(photo.backup||'位置记录缺失'));
      const actions=document.createElement('div');actions.className='row';const restore=document.createElement('button');restore.textContent='恢复这张';restore.className='restore-photo';restore.onclick=()=>enqueue(()=>restoreSpecific(photo.id));
      const open=document.createElement('button');open.textContent='打开位置';open.onclick=()=>openFolder(parentOf(photo.backup));actions.append(restore,open);
      card.append(preview,title,original,location,actions);$('gridCards').append(card);continue;
    }
    const button=document.createElement('button');button.className='card';const img=document.createElement('img');img.loading='lazy';img.decoding='async';img.alt=photo.name;img.src=imageUrl(photo,320);
    const label=document.createElement('small');label.textContent=(photo.status===1?'✓ ':photo.status===-1?'× ':'')+photo.name;
    button.append(img,label);button.title=photo.relative;button.onclick=()=>enqueue(()=>show(photo));$('gridCards').append(button);
  }
  $('gridPrev').disabled=gridIndex===0;$('gridNext').disabled=gridRows.length<60;
  $('gridPage').textContent=gridRows.length?'第 '+(gridIndex+1)+' 页 · '+gridRows.length+' 张':'队列中没有照片';$('grid').scrollTop=0;
}
function closeDetail(){
  if($('detail').hidden)return;
  detailVersion++;detailPhoto=null;$('detail').hidden=true;
  $('detailImage').removeAttribute('src');$('detailImage').onload=$('detailImage').onerror=null;
  detailPreviousFocus?.focus({preventScroll:true});
}
function openDetail(photo,event){
  if(!photo)return;
  const revision=++detailVersion;detailPhoto=photo;detailPreviousFocus=document.activeElement;
  const sourceElement=event?.currentTarget;
  const rect=sourceElement?.getBoundingClientRect();
  const x=rect?Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)):.5;
  const y=rect?Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height)):.5;
  $('detail').hidden=false;$('detailClose').focus();$('detailImage').hidden=true;
  $('detailStatus').hidden=false;$('detailStatus').textContent='正在读取完整分辨率…';$('detailTitle').textContent=photo.name+' · 原尺寸 1:1 · 滚动查看 / Esc 返回';
  const image=$('detailImage');
  image.onload=()=>{
    if(revision!==detailVersion)return;
    image.hidden=false;$('detailStatus').hidden=true;$('detailTitle').textContent=photo.name+' · '+image.naturalWidth+' × '+image.naturalHeight+' · 1:1 像素 · 滚动查看';
    requestAnimationFrame(()=>{if(revision!==detailVersion)return;const viewport=$('detailViewport');viewport.scrollLeft=Math.max(0,x*image.naturalWidth-viewport.clientWidth/2);viewport.scrollTop=Math.max(0,y*image.naturalHeight-viewport.clientHeight/2);});
  };
  image.onerror=()=>{if(revision!==detailVersion)return;$('detailStatus').textContent='无法读取完整分辨率，请检查文件格式或硬盘连接。';};
  image.src='/api/detail/'+photo.id+'?v='+photo.mtime+'&token='+encodeURIComponent(token||'');
}
$('photo').onclick=event=>openDetail(current,event);$('nextPhoto').onclick=event=>openDetail(upcoming,event);
$('zoom').onclick=()=>openDetail(current);$('detailClose').onclick=closeDetail;
$('detail').addEventListener('click',event=>{if(event.target===$('detail'))closeDetail();});
$('scan').onclick=()=>enqueue(async()=>{await api('scan',{path:$('folder').value.trim()});await refreshState();});
$('dedup').onclick=()=>enqueue(async()=>{await api('duplicates',{});await refreshState();});
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>enqueue(()=>selectView(b.dataset.view)));
document.querySelectorAll('[data-export]').forEach(b=>b.onclick=()=>{
  const a=document.createElement('a');a.href='/api/export?view='+b.dataset.export+'&token='+encodeURIComponent(token||'');a.download='photos-'+b.dataset.export+'.csv';document.body.append(a);a.click();a.remove();
});
$('keep').onclick=()=>enqueue(()=>mark(1));$('delete').onclick=()=>enqueue(deleteCurrent);
$('undo').onclick=()=>enqueue(()=>undo());$('restore').onclick=()=>enqueue(()=>undo(true));
$('previous').onclick=()=>enqueue(()=>navigate('prev'));$('next').onclick=()=>enqueue(()=>navigate('next'));
$('stage').addEventListener('contextmenu',event=>{event.preventDefault();enqueue(()=>navigate('next'));});
$('gridToggle').onclick=()=>enqueue(()=>{if(view==='deleted'){gridMode=false;return selectView('pending');}return gridMode?(current?show(current):first()):loadGrid();});
$('gridPrev').onclick=()=>enqueue(async()=>{if(gridIndex>0){gridIndex--;await loadGrid();}});
$('gridNext').onclick=()=>enqueue(async()=>{if(gridRows.length){gridAnchors[++gridIndex]=gridRows.at(-1).id;await loadGrid();}});
document.addEventListener('keydown',event=>{
  const key=event.key.toLowerCase();
  if(key==='escape'){if(!$('saveDialog').hidden)$('saveDialog').hidden=true;else closeDetail();return;}
  if(['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName)||event.altKey||!$('saveDialog').hidden)return;
  if((event.ctrlKey||event.metaKey)&&key==='z'){event.preventDefault();if(!event.repeat){closeDetail();enqueue(()=>undo());}return;}
  if(event.ctrlKey||event.metaKey)return;
  if(key==='arrowleft'){event.preventDefault();if(!event.repeat){closeDetail();enqueue(()=>undo(true));}return;}
  if(gridMode||!current)return;
  const action={arrowup:()=>mark(1),p:()=>mark(1),arrowdown:deleteCurrent,arrowright:()=>navigate('next')}[key];
  if(action){event.preventDefault();if(!event.repeat){closeDetail();enqueue(action);}}
  else if(key==='z'){event.preventDefault();if($('detail').hidden)openDetail(current);else closeDetail();}
});
$('viewDeleted').onclick=()=>enqueue(()=>selectView('deleted'));
$('openLastDelete').onclick=()=>openFolder(parentOf(lastDeleted?.backup));
$('restoreLastDelete').onclick=()=>enqueue(()=>restoreSpecific(lastDeleted.id));
$('saveKept').onclick=openSave;$('saveClose').onclick=()=>{$('saveDialog').hidden=true;};
$('saveDialog').addEventListener('click',event=>{if(event.target===$('saveDialog'))$('saveDialog').hidden=true;});
$('chooseDestination').onclick=async()=>{
  $('chooseDestination').disabled=true;
  try{const result=await api('choose-folder');if(result.path)$('destination').value=result.path;}
  catch(error){toast(error.message);}finally{$('chooseDestination').disabled=false;}
};
$('copyKept').onclick=()=>enqueue(()=>saveKept('copy'));$('moveKept').onclick=()=>enqueue(()=>saveKept('move'));
$('openDestination').onclick=()=>openFolder(state?.last_export?.destination||$('destination').value.trim());
async function init(){
  if(!token){toast('请从启动脚本打开，或使用终端里包含 #token 的完整本机地址。');return;}
  updateView();await refreshState(false);let cursor=state?.cursor||0;
  try{const stored=JSON.parse(localStorage.getItem('photo-sieve-cursor'));if(stored?.root===state?.root)cursor=stored.id;}catch{}
  if(cursor){try{const photo=await api('photo/'+cursor);if(eligible(photo))await show(photo);else await first();}catch{await first();}}
  else await first();
  initialized=true;setInterval(refreshState,2500);
}
init().catch(error=>toast(error.message));
