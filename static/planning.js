"use strict";
// Planning intentions are project-private. Model output is always a reviewable draft.
(() => {
  const inputLocks=new WeakMap();
  async function planningAction(action,event){
    const controls=[...document.querySelectorAll("#definition-workspace input,#definition-workspace textarea,#definition-workspace select,#definition-workspace button,#baseline-workspace input,#baseline-workspace textarea,#baseline-workspace select,#baseline-workspace button")];
    for(const input of controls){const lock=inputLocks.get(input)||{count:0,disabled:input.disabled};lock.count++;inputLocks.set(input,lock);input.disabled=true;}
    try{return await action(event);}finally{for(const input of controls){const lock=inputLocks.get(input);if(--lock.count===0){input.disabled=lock.disabled;inputLocks.delete(input);}}}
  }
  function planningGuard(action){return guard(event=>planningAction(action,event));}
  function planningButton(label,action,cls){return button(label,event=>planningAction(action,event),cls);}
  const stages = {product:"2.1 프로덕트정의",stories:"2.2 사용자스토리정의",requirements:"2.3 요구사항 정의",constraints:"2.4 비기능요구사항 정의",goals:"2.5 목표설명",prd:"2.6 PRD생성"};
  const labels = {title:"제목",actor:"사용자·역할",situation:"상황",action:"원하는 행동",value:"기대 가치",relationship:"관련 사용자·관계",problem:"관련 문제",validation_task:"문제 가설 검증 과제",priority:"우선순위",epic:"상위 에픽",journey:"사용자 여정",release:"릴리스",mvp:"MVP 포함",new_problem:"새 문제 가설",scenarios:"정상·예외 흐름",acceptance_criteria:"수용 기준",questions:"확인 질문",assumptions:"가정"};
  let p = {stage:"product",assets:[],stories:[],documents:[],results:[],drafts:[],runs:{},extractions:[],selectedRuns:[],recovery:null,story:null,document:null,activeAsset:null,activeRun:null};
  let serial=0, promptAction=null, editorFields={}, canvasView=null;
  function pf(root,key,label,value="",type="textarea") {
    const id="planning-field-"+(++serial), input=node(type==="select"?"select":type==="checkbox"?"input":type==="text"?"input":"textarea");
    input.id=id;input.name=key;
    if(type==="checkbox"){input.type="checkbox";input.checked=!!value;}else input.value=value??"";
    const l=node("label",label);l.htmlFor=id;root.append(l,input);return input;
  }
  function pick(root,key,label,options,selected="",multiple=false){
    const input=pf(root,key,label,"","select");input.multiple=multiple;
    for(const [value,title] of options){const option=node("option",title);option.value=value;option.selected=multiple?(selected||[]).includes(value):value===selected;input.append(option);}return input;
  }
  function values(select){return [...select.selectedOptions].map(x=>x.value);}
  function checked(input){return input.type==="checkbox"?input.checked:input.value;}
  function lines(input){return input.value.split("\n").map(s=>s.trim()).filter(Boolean);}
  function download(name,content,type="text/markdown;charset=utf-8"){
    const url=URL.createObjectURL(new Blob([content],{type})),a=node("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function exportButtons(root,path,body,name){
    root.append(planningButton("Markdown 내보내기",async()=>{const out=await api(path,body);download(name+".md",out.markdown);}),
      planningButton("구조화 설계안 내보내기",async()=>{const out=await api(path,body);download(name+".json",JSON.stringify(out.package,null,2),"application/json");}));
  }
  function prompt(title,action){
    promptAction=action;$("draft-prompt-title").textContent=title;$("draft-prompt").value="";
    const dialog=$("draft-prompt-dialog");if(dialog.showModal)dialog.showModal();else dialog.setAttribute("open","");$("draft-prompt").focus();
  }
  function closePrompt(){const dialog=$("draft-prompt-dialog");if(dialog.close)dialog.close();else dialog.removeAttribute("open");promptAction=null;}
  $("draft-prompt-cancel").onclick=closePrompt;
  $("draft-prompt-form").onsubmit=planningGuard(async()=>{
    const intent=$("draft-prompt").value.trim();if(!intent)throw new Error("이번 초안의 의도를 입력하세요.");
    const action=promptAction;if(!action)return;notice("입력한 의도로 초안을 작성 중입니다.");await action(intent);closePrompt();notice("초안을 저장했습니다. 출처와 확인 질문을 검토하세요.");
  });
  function repeat(root,title,initial,specs){
    const details=node("details");details.open=true;details.append(node("summary",title));root.append(details);
    const list=node("div");details.append(list);const rows=[];
    function add(row={}){
      const wrap=node("div",null,"planning-entry"),fields={};list.append(wrap);
      for(const [key,label,type,opts] of specs)fields[key]=type==="select"?pick(wrap,key,label,opts,row[key]??opts[0][0]):pf(wrap,key,label,Array.isArray(row[key])?row[key].join("\n"):row[key]??"",type);
      wrap.append(planningButton("항목 삭제",()=>wrap.remove()));rows.push({wrap,fields,row});
    }
    initial.forEach(add);details.append(planningButton(title+" 추가",()=>add()));
    return ()=>rows.filter(r=>r.wrap.parentNode).map(({fields,row})=>{
      const out={...row};for(const [key,,type] of specs)out[key]=type==="lines"?lines(fields[key]):checked(fields[key]);return out;
    });
  }
  async function load(){
    const [assets,stories,defs,results,drafts,runs]=await Promise.all([api("/api/planning-assets"),api("/api/stories"),api("/api/definitions"),api("/api/research-results"),api("/api/story-drafts"),api("/api/planning-extractions")]);
    Object.assign(p,{assets,stories,documents:defs.documents,results,drafts,extractions:runs.filter(r=>r.status==="completed")});
    p.selectedRuns=p.selectedRuns.filter(id=>p.extractions.some(r=>r.id===id));
    p.runs={};for(const run of runs)if(run.status==="completed")p.runs[run.asset_id]=run;
    if(p.story)p.story=stories.find(r=>r.id===p.story.id&&!r.redacted)||null;
    if(p.document)p.document=defs.documents.find(r=>r.id===p.document.id&&!r.redacted)||null;
  }
  function mobileTabs(root,panels){
    const tabs=node("div",null,"mobile-planning-tabs");root.append(tabs);
    function select(index){panels.forEach((panel,i)=>panel.classList.toggle("mobile-inactive",i!==index));}
    ["원본","스토리 편집","검토·확정"].forEach((name,i)=>tabs.append(planningButton(name,()=>select(i))));select(1);return select;
  }
  function uploadForm(root,purpose,onSaved,previous=null){
    const form=node("form"),title=pf(form,previous?"revision-title":"asset-title",previous?"교체 자료 제목":"기획 자료 제목",previous?.title||"","text"),file=pf(form,previous?"revision-file":"asset-file",previous?"교체할 파일":"PNG·JPEG·문서 / 최대 3MB","","text");
    file.type="file";file.accept=purpose==="story_sketch"?".png,.jpg,.jpeg,.md,.txt,.pdf,.docx,.pptx":".md,.txt,.pdf,.docx,.pptx";
    title.required=true;file.required=true;const submit=node("button",previous?"원본 새 버전 저장":"기획 자료 업로드","primary");submit.type="submit";form.append(submit);root.append(form);
    form.onsubmit=planningGuard(async()=>{const row=await api("/api/planning-assets",{...await fileBody(file),title:title.value,purpose,...(previous?{asset_id:previous.id,expected_version:previous.version}:{})});
      await load();await onSaved?.(row);notice(row.duplicate?"동일 원본이 있어 기존 자료를 선택했습니다. 분석은 별도로 실행하세요.":"현재 프로젝트의 기획 자료로 저장했습니다.");});
  }
  async function sourcePanel(root){
    root.replaceChildren(node("h2","기획 원본"),node("p","손그림과 기획 문서는 실제 고객 근거와 별도로 보관합니다."));
    uploadForm(root,"story_sketch",async(row)=>{p.activeAsset=row.id;p.activeRun=null;await sourcePanel(root);});
    const select=pick(root,"planning-source","원본 선택",[["","선택하세요"],...p.assets.map(a=>[a.id,a.title+" · v"+a.version])],p.activeAsset||"");
    const view=node("div");root.append(view);
    select.onchange=planningGuard(async()=>{p.activeAsset=select.value||null;p.activeRun=null;await showSource(view,root);});await showSource(view,root);
  }
  async function sourceSnapshot(root,assetId,version,bbox=null){
    const raw=await api("/api/planning-assets/raw/"+assetId+"?version="+version),box=node("div",null,"planning-entry");
    box.append(node("h4","원본 v"+raw.version));root.append(box);
    if(raw.media_type==="image"){
      const canvas=node("canvas"),img=new Image();canvas.setAttribute("aria-label","이전 원본 v"+version);box.append(canvas);
      img.onload=()=>{if(!canvas.isConnected)return;canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;const ctx=canvas.getContext("2d");ctx.drawImage(img,0,0);if(bbox){const [x,y,w,h]=bbox;ctx.strokeStyle="#d26512";ctx.lineWidth=Math.max(canvas.width,canvas.height)/150;ctx.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);}};
      img.src="data:"+raw.mime+";base64,"+raw.content_base64;
    }else box.append(node("pre",raw.text));
  }
  async function showSource(view,root){
    view.replaceChildren();canvasView=null;const asset=p.assets.find(a=>a.id===p.activeAsset);if(!asset)return;
    view.append(node("p",asset.filename+" · 원본 v"+asset.version+" · "+asset.sharing,"micro"));
    const raw=await api("/api/planning-assets/raw/"+asset.id),runs=p.extractions.filter(r=>r.asset_id===asset.id),run=runs.find(r=>r.id===p.activeRun)||p.runs[asset.id];
    if(p.activeAsset!==asset.id||!view.isConnected)return;
    if(runs.length){const select=pick(view,"source-run","표시할 분석 이력",runs.map((r,i)=>[r.id,"분석 "+(i+1)+(r.origin==="po_edited"?" · PO 수정":" · AI/전사")+" · "+r.id.slice(0,8)]),run?.id);select.onchange=planningGuard(async()=>{p.activeRun=select.value;await showSource(view,root);});}
    const scroll=node("div",null,"source-scroll"),regionList=node("div");view.append(scroll);let imageOptions=()=>({});
    if(raw.media_type==="image"){
      const canvas=node("canvas");canvas.setAttribute("aria-label","기획 원본과 분석 영역");canvas.style.touchAction="none";scroll.append(canvas);
      const img=new Image(),generation=state.generation;
      const v={canvas,img,rotation:0,selected:null,regions:run?.regions||[],crop:[0,0,1,1],selecting:false};canvasView=v;
      img.onload=()=>{if(generation===state.generation&&canvasView===v)drawSource();};img.src="data:"+raw.mime+";base64,"+raw.content_base64;
      const controls=node("div",null,"planning-toolbar"),status=node("p","분석 범위: 전체 원본 · 회전 0°","micro");view.append(controls,status);
      const coordinates=node("div",null,"planning-entry"),cropInputs=["x","y","width","height"].map((key,i)=>{const field=pf(coordinates,"crop-"+key,["영역 X (0~1)","영역 Y (0~1)","영역 너비 (0~1)","영역 높이 (0~1)"][i],v.crop[i],"text");field.type="number";field.min="0";field.max="1";field.step="0.001";return field;});
      const sync=()=>{cropInputs.forEach((input,i)=>input.value=Number(v.crop[i].toFixed(6)));status.textContent="분석 범위: ["+v.crop.map(n=>n.toFixed(3)).join(", ")+"] · 회전 "+v.rotation*90+"°";drawSource();};
      controls.append(planningButton("원본 회전 90°",()=>{v.rotation=(v.rotation+1)%4;sync();}),planningButton("확대/축소",()=>scroll.classList.toggle("zoomed")),planningButton("분석 영역 드래그 선택",()=>{v.selecting=true;status.textContent="원본에서 분석할 사각형을 드래그하세요.";}),planningButton("전체 영역 복원",()=>{v.crop=[0,0,1,1];sync();}));
      coordinates.append(planningButton("분석 영역 좌표 적용",()=>{const next=cropInputs.map(input=>Number(input.value));if(next.some(n=>!Number.isFinite(n))||next[0]<0||next[1]<0||next[2]<=0||next[3]<=0||next[0]+next[2]>1.000001||next[1]+next[3]>1.000001)throw new Error("이미지 안의 0~1 좌표를 입력하세요.");v.crop=next;sync();}));view.append(coordinates);
      const point=event=>{const box=canvas.getBoundingClientRect();let x=(event.clientX-box.left)/box.width,y=(event.clientY-box.top)/box.height;
        if(v.rotation===1)[x,y]=[y,1-x];else if(v.rotation===2)[x,y]=[1-x,1-y];else if(v.rotation===3)[x,y]=[1-y,x];return [Math.max(0,Math.min(1,x)),Math.max(0,Math.min(1,y))];};
      let start=null;
      canvas.onpointerdown=event=>{if(v.selecting){start=point(event);canvas.setPointerCapture(event.pointerId);}};
      canvas.onpointermove=event=>{if(!start)return;const end=point(event);v.crop=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(end[0]-start[0]),Math.abs(end[1]-start[1])];sync();};
      canvas.onpointerup=event=>{if(start){const end=point(event);v.crop=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.abs(end[0]-start[0]),Math.abs(end[1]-start[1])];if(!v.crop[2]||!v.crop[3])v.crop=[0,0,1,1];start=null;v.selecting=false;sync();return;}
        const [x,y]=point(event),region=v.regions.find(r=>{const [rx,ry,rw,rh]=r.bbox;return x>=rx&&x<=rx+rw&&y>=ry&&y<=ry+rh;});if(region)highlightRegion(asset.id,region.id);};
      canvas.onpointercancel=()=>{start=null;v.selecting=false;v.crop=[0,0,1,1];sync();};
      imageOptions=()=>({crop:v.crop,rotation:v.rotation*90});
    }else scroll.append(node("pre",raw.text));
    view.append(planningButton("원본 분석 · AI초안작성",()=>prompt("원본 전사·영역 분석",async intent=>{
      const saved=await api("/api/planning-assets/extract",{asset_id:asset.id,expected_version:asset.version,prompt:intent,...imageOptions()});p.activeRun=saved.id;await load();renderStage();
    })),planningButton("원본 철회",async()=>{await api("/api/planning-assets/withdraw",{asset_id:asset.id,expected_version:asset.version});p.activeAsset=null;await load();renderStage();}));
    const revision=node("details");revision.append(node("summary","원본 교체 · 이전 버전 보존"));view.append(revision);
    uploadForm(revision,asset.purpose,async()=>{p.activeRun=null;renderStage();},asset);
    if(run){
      view.append(node("h3","전사·관계·확인 질문"),node("pre",run.transcript),regionList);
      if(run.view)view.append(node("p","이 분석의 원본 범위: ["+run.view.crop.join(", ")+"] · 회전 "+run.view.rotation+"°","micro"));
      run.regions.forEach(r=>regionList.append(planningButton(r.id+" · "+r.kind+" · "+r.text.slice(0,90),()=>highlightRegion(asset.id,r.id))));
      run.relations.forEach(r=>view.append(node("p",r.from_region+" → "+r.to_region+": "+r.meaning+(r.uncertain?" · 확인 필요":""))));
      [...run.quality_issues,...run.questions.map(q=>q.text)].forEach(t=>view.append(node("p","확인 필요: "+t)));
      view.append(planningButton("이 분석을 스토리 입력에 추가",()=>{selectRun(run.id);renderStage();}));
      regionEditor(view,run);
    }
  }
  function regionEditor(root,run){
    const details=node("details");details.append(node("summary","영역·전사 직접 편집"));root.append(details);
    const transcript=pf(details,"region-transcript","전사 수정",run.transcript);
    const rows=repeat(details,"원본 영역",run.regions.map(r=>({...r,coords:r.bbox.join(",")})),[["id","영역 ID","text"],["coords","원본 좌표 x,y,너비,높이 (0~1)","text"],["text","영역 전사","textarea"],["kind","영역 종류","select",["text","box","arrow","branch","crossout","question","unreadable"].map(k=>[k,k])]]);
    const relations=repeat(details,"영역 관계",run.relations,[["from_region","시작 영역 ID","text"],["to_region","끝 영역 ID","text"],["meaning","관계 의미","textarea"],["uncertain","관계 확인 필요","checkbox"]]);
    const note=pf(details,"region-note","영역 수정 이유");
    details.append(planningButton("새 영역 검토본 저장",async()=>{const saved=await api("/api/planning-assets/regions",{extraction_id:run.id,expected_version:run.version,transcript:transcript.value,regions:rows().map(r=>({...r,bbox:r.coords.split(",").map(Number)})),relations:relations(),note:note.value});p.activeRun=saved.id;await load();renderStage();notice("이전 분석을 보존하고 영역 검토본을 저장했습니다. 사용할 분석을 입력 목록에서 선택하세요.");}));
  }
  function selectRun(id){
    const run=p.extractions.find(r=>r.id===id);if(!run)throw new Error("완료한 분석을 선택하세요.");
    if(p.selectedRuns.includes(id))return;
    if(p.selectedRuns.length>=3)throw new Error("이미지는 최대 3개까지 선택하세요.");
    if(p.selectedRuns.some(rid=>p.extractions.find(r=>r.id===rid)?.asset_id===run.asset_id))throw new Error("같은 원본의 분석은 하나만 선택하세요. 기존 입력을 제거한 뒤 추가하세요.");
    p.selectedRuns.push(id);
  }
  function extractionOrder(root){
    const box=node("div",null,"planning-entry");root.append(box);box.append(node("h3","분석 입력 · 선택한 순서로 사용"),node("p","최대 3개 원본. 선택하지 않으면 이미지 분석을 입력에 넣지 않습니다.","micro"));
    const choice=pick(box,"analysis-input","사용할 분석",[["","선택하세요"],...p.extractions.map(r=>[r.id,(p.assets.find(a=>a.id===r.asset_id)?.title||r.asset_id)+" · v"+r.asset_version+" · "+r.id.slice(0,8)])]);
    box.append(planningButton("선택한 분석 추가",()=>{selectRun(choice.value);renderStage();}));
    p.selectedRuns.forEach((id,index)=>{const run=p.extractions.find(r=>r.id===id),entry=node("div",null,"analysis-order-entry");entry.dataset.extractionId=id;entry.append(node("p",(index+1)+". "+(p.assets.find(a=>a.id===run.asset_id)?.title||run.asset_id)+" · "+id.slice(0,8)));
      const up=planningButton("입력 위로",()=>{[p.selectedRuns[index-1],p.selectedRuns[index]]=[id,p.selectedRuns[index-1]];renderStage();}),down=planningButton("입력 아래로",()=>{[p.selectedRuns[index+1],p.selectedRuns[index]]=[id,p.selectedRuns[index+1]];renderStage();});up.disabled=index===0;down.disabled=index===p.selectedRuns.length-1;
      entry.append(up,down,planningButton("입력 제거",()=>{p.selectedRuns=p.selectedRuns.filter(rid=>rid!==id);renderStage();}));box.append(entry);
    });
  }
  function drawSource(){
    const v=canvasView;if(!v||!v.img.naturalWidth)return;const {canvas,img,rotation}=v,w=img.naturalWidth,h=img.naturalHeight;
    canvas.width=rotation%2?h:w;canvas.height=rotation%2?w:h;const ctx=canvas.getContext("2d");ctx.save();
    if(rotation===1){ctx.translate(h,0);ctx.rotate(Math.PI/2);}if(rotation===2){ctx.translate(w,h);ctx.rotate(Math.PI);}if(rotation===3){ctx.translate(0,w);ctx.rotate(-Math.PI/2);}
    ctx.drawImage(img,0,0);for(const r of v.regions){const [x,y,rw,rh]=r.bbox;ctx.strokeStyle=r.id===v.selected?"#d26512":"#137157";ctx.lineWidth=Math.max(w,h)/250;ctx.strokeRect(x*w,y*h,rw*w,rh*h);}
    if(v.crop){const [x,y,cw,ch]=v.crop;ctx.strokeStyle="#285acb";ctx.setLineDash([w/40,w/60]);ctx.strokeRect(x*w,y*h,cw*w,ch*h);}ctx.restore();
  }
  function highlightRegion(assetId,regionId){
    if(canvasView){canvasView.selected=regionId;drawSource();}
    for(const [key,input] of Object.entries(editorFields)){const ref=p.story?.provenance?.[key],match=ref?.asset_id===assetId&&ref?.region_id===regionId&&!ref.historical_source;input.classList.toggle("source-selected",!!match);if(match)input.focus();}
  }
  function recoveryEditor(root){
    const recovery=p.recovery,row=recovery.story,panel=node("div",null,"panel");root.append(panel);
    panel.append(node("h2","원본 변경 비교 · 스토리 재검토"),node("p",row.title+" · v"+row.version),node("p",recovery.disclosure),node("pre",[row.actor,row.action,row.value].join(" / ")));
    for(const change of recovery.changes){const box=node("div",null,"planning-entry");panel.append(box);box.append(node("h3",change.current.title+" · v"+change.previous.version+" → v"+change.current.version),node("p","파일: "+change.previous.filename+" → "+change.current.filename),node("p",change.previous.hash===change.current.hash?"원본 파일 내용 동일 · 메타데이터 변경":"원본 파일 내용 변경"));
      box.append(planningButton("이전·현재 원본 비교",async()=>{const out=node("div",null,"source-comparison");box.append(out);await sourceSnapshot(out,change.previous.id,change.previous.version);await sourceSnapshot(out,change.current.id,change.current.version);}));}
    const note=pf(panel,"source-review-note","원본 변경 검토 메모"),ack=pf(panel,"source-review-ack","이전 원본과 현재 원본의 변경 내용을 확인했습니다",false,"checkbox");
    panel.append(planningButton("PO 편집을 유지한 재검토 초안 저장",async()=>{p.story=await api("/api/stories/recover",{story_id:row.id,expected_version:row.version,review_id:recovery.review_id,note:note.value,acknowledged:ack.checked});p.recovery=null;await load();renderStage();notice("기존 PO 편집을 유지했습니다. 추가된 핵심 확인 질문을 검토한 후 다시 확정하세요.");}),planningButton("재검토 취소",()=>{p.recovery=null;renderStage();}));
  }
  function storyEditor(root){
    if(p.recovery){recoveryEditor(root);return;}
    const toolbar=node("div",null,"planning-toolbar");root.append(toolbar);
    toolbar.append(planningButton("새 스토리 직접 작성",()=>{p.story=null;renderStage();}),planningButton("스토리 맵",()=>renderStoryMap(root)));
    const list=node("div",null,"planning-list");root.append(list);
    for(const story of p.stories)list.append(planningButton(story.redacted?"재검토 필요 · "+story.id.slice(0,12):story.title+" · v"+story.version+" · "+story.definition_status,async()=>{if(story.redacted){p.recovery=await api("/api/stories/recovery-preview",{story_id:story.id,expected_version:story.version});}else p.story=story;renderStage();}));
    const grid=node("div",null,"planning-grid"),source=node("div",null,"panel planning-source"),editor=node("div",null,"panel planning-editor"),review=node("div",null,"panel planning-review");
    mobileTabs(root,[source,editor,review]);root.append(grid);grid.append(source,editor,review);planningGuard(()=>sourcePanel(source))();
    const row=p.story||{},form=node("form");editor.append(node("h2",row.id?"스토리 수정":"스토리 직접 작성"),form);editorFields={};
    for(const key of Object.keys(labels).filter(k=>!["scenarios","acceptance_criteria","questions","assumptions"].includes(k))){
      const input=pf(form,key,labels[key],row[key],['mvp','new_problem'].includes(key)?"checkbox":"textarea");editorFields[key]=input;
      if(key==="actor"||key==="action")input.required=true;
      const ref=row.provenance?.[key];if(ref){const origin={extracted:"원본 추출",ai_proposed:"AI 제안",po_edited:"PO 수정"}[ref.origin]||ref.origin;
        form.append(planningButton(origin+(ref.asset_id?" · 원본 영역 보기":""),async()=>{if(ref.asset_id){if(ref.historical_source){await sourceSnapshot(source,ref.asset_id,ref.asset_version,ref.bbox);notice("이전 원본의 출처입니다. 현재 원본과 다시 비교하세요.");return;}p.activeAsset=ref.asset_id;p.activeRun=ref.extraction_id||null;await sourcePanel(source);highlightRegion(ref.asset_id,ref.region_id);}notice("이전 값: "+String(ref.original_value??""));},"provenance"));}
    }
    const assumptions=pf(form,"assumptions","가정 · 한 줄에 하나",(row.assumptions||[]).join("\n"));
    const scenarios=repeat(form,"시나리오",row.scenarios||[],[["type","흐름 종류","select",[["normal","정상"],["exception","예외"]]],["title","시나리오 이름","text"],["steps","진행 단계 · 한 줄에 하나","lines"],["branch","분기·예외 조건","textarea"]]);
    const criteria=repeat(form,"수용 기준",row.acceptance_criteria||[],[["given","Given · 전제","textarea"],["when","When · 행동","textarea"],["then","Then · 관찰 가능한 결과","textarea"]]);
    const questions=repeat(form,"확인 질문",row.questions||[],[["text","질문","textarea"],["critical","핵심 질문","checkbox"],["status","처리 상태","select",[["unanswered","미해소"],["answered","답변 완료"],["deferred","나중에 확인"],["excluded","범위 제외"]]],["answer","답변·제외 사유","textarea"]]);
    const evidence=pick(form,"story-evidence","실제 근거·공유 인사이트 연결",state.evidence.map(e=>[e.id,(e.title||e.text).slice(0,70)+" · "+e.evidence_type]),row.evidence_ids||[],true);
    const sources=pick(form,"story-sources","연결할 기획 원본",p.assets.map(a=>[a.id,a.title+" · v"+a.version]),(row.source_refs||[]).map(r=>r.id),true);
    const docs=p.results.filter(r=>!r.redacted&&r.state==="reviewed");
    const research=pick(form,"story-research","검토한 리서치 결과 연결",docs.map(r=>[r.id,r.title]),(row.document_refs||[]).filter(r=>r.kind==="research_result").map(r=>r.id),true);
    const submit=node("button","초안 저장","primary");submit.type="submit";form.append(submit);
    form.onsubmit=planningGuard(async()=>{
      const fields=Object.fromEntries(Object.entries(editorFields).map(([k,v])=>[k,checked(v)]));
      p.story=await api(row.id?"/api/stories/update":"/api/stories",{...fields,assumptions:lines(assumptions),scenarios:scenarios(),acceptance_criteria:criteria(),questions:questions(),
        evidence_ids:values(evidence),document_refs:[...(row.document_refs||[]).filter(r=>r.kind!=="research_result"),...docs.filter(r=>values(research).includes(r.id)).map(r=>({kind:r.kind,id:r.id,version:r.version}))],
        source_refs:p.assets.filter(a=>values(sources).includes(a.id)).map(a=>({id:a.id,version:a.version})),persona_refs:row.persona_refs||[],...(row.id?{story_id:row.id,expected_version:row.version}:{})});await load();renderStage();notice("설계 초안을 저장했습니다. 고객 검증 상태와는 별개입니다.");
    });
    review.append(node("h2","검토·확정"),node("p",row.id?row.id+"\nv"+row.version+" · 설계: "+row.definition_status+"\n고객 검증: "+row.customer_validation:"사용자·행동을 입력하면 초안을 저장할 수 있습니다.","planning-status"));
    extractionOrder(review);
    review.append(planningButton("AI초안작성 · 새 후보/재분석",()=>prompt("사용자 스토리 AI초안작성",async intent=>{
      await api("/api/story-drafts",{prompt:intent,extraction_ids:[...p.selectedRuns],
        ...(row.id?{base_story_id:row.id}:{}),evidence_ids:row.evidence_ids||[],source_refs:row.source_refs||[],persona_refs:row.persona_refs||[],document_refs:row.document_refs||[]});await load();renderStage();
    })));
    if(row.id){
      const reason=pf(review,"review-reason","검토 메모 · 보류/제외 시 필수");
      for(const [stateName,label] of [["in_review","검토 중"],["held","보류"],["excluded","범위 제외"],["confirmed","이 버전 설계안 확정"]])review.append(planningButton(label,async()=>{
        p.story=await api("/api/stories/review",{story_id:row.id,expected_version:row.version,state:stateName,reason:reason.value});await load();renderStage();}));
      const validation=pick(review,"customer-validation","고객 검증 상태",[["unverified","미검증"],["planned","검증 예정"],["actual_results","실제 결과 있음"]],row.customer_validation);
      const actualReports=pick(review,"validation-reports","실제 조사로 확인한 FGI 검토본",p.results.filter(r=>!r.redacted&&r.actual_customer_data&&r.state==="reviewed").map(r=>[r.id,r.title]),row.validation_result_ids||[],true);
      const validationNote=pf(review,"validation-note","고객 검증 메모");
      review.append(planningButton("고객 검증 상태 저장",async()=>{p.story=await api("/api/stories/validation",{story_id:row.id,expected_version:row.version,state:validation.value,note:validationNote.value,evidence_ids:values(evidence),result_ids:values(actualReports)});await load();renderStage();}));
      if(row.definition_status==="confirmed")exportButtons(review,"/api/stories/export",{stories:[{id:row.id,version:row.version}]},row.id+"-v"+row.version);
      review.append(planningButton("이전 버전 확인",async()=>{
        const history=await api("/api/stories/versions/"+row.id),container=node("div");
        for(const version of history){const card=node("details");card.append(node("summary","v"+version.version+(version.redacted?" · 재확인 필요":" · "+version.definition_status)));
          card.append(node("p",version.redacted?version.text:[version.actor,version.action,version.value].join(" / ")));if(!version.redacted&&version.definition_status==="confirmed")exportButtons(card,"/api/stories/export",{stories:[{id:row.id,version:version.version}]},row.id+"-v"+version.version);container.append(card);}review.append(container);
      }));
      review.append(planningButton("이 스토리 분할",()=>{
        const box=node("div",null,"planning-entry"),a=pf(box,"split-a","첫 번째 스토리 행동"),b=pf(box,"split-b","두 번째 스토리 행동");
        box.append(planningButton("별도 ID로 분할 저장",async()=>{await api("/api/stories/restructure",{sources:[{id:row.id,version:row.version}],stories:[{action:a.value,title:a.value},{action:b.value,title:b.value}]});await load();renderStage();}));review.append(box);
      }));
      const merge=pick(review,"merge-story","병합할 다른 스토리",[["","선택하세요"],...p.stories.filter(s=>s.id!==row.id&&!s.redacted).map(s=>[s.id,s.title])]);
      review.append(planningButton("새 ID로 병합 초안 작성",async()=>{const other=p.stories.find(s=>s.id===merge.value);if(!other)throw new Error("병합할 스토리를 선택하세요.");await api("/api/stories/restructure",{sources:[{id:row.id,version:row.version},{id:other.id,version:other.version}],stories:[{title:row.title+" / "+other.title,action:row.action+"; "+other.action}]});await load();renderStage();}));
    }
    renderCandidates(review,row);
  }
  function renderCandidates(root,row){
    const candidates=p.drafts.filter(d=>row.id?d.base_story?.id===row.id:!d.base_story).slice(-8);
    for(const draft of candidates)draft.stories.forEach((candidate,index)=>{
      const box=node("details",null,"planning-entry");box.append(node("summary","AI 후보 · "+candidate.title),node("p",draft.prompt));const fields=[];
      for(const [key,label] of Object.entries(labels)){
        const changed=!row.id||JSON.stringify(row[key])!==JSON.stringify(candidate[key]);if(!changed)continue;
        const choice=pf(box,"adopt-"+key,label,false,"checkbox");fields.push([key,choice]);
        const display=v=>Array.isArray(v)?v.map(x=>typeof x==="object"?Object.values(x).join(" · "):x).join("\n"):String(v??"");
        if(row.id)box.append(node("pre","현재: "+display(row[key])));box.append(node("pre","후보: "+display(candidate[key])));
      }
      box.append(planningButton(row.id?"선택한 필드만 채택":"새 스토리 초안으로 채택",async()=>{
        p.story=await api("/api/story-drafts/apply",{draft_id:draft.id,candidate_index:index,...(row.id?{story_id:row.id,expected_version:row.version,fields:fields.filter(([,e])=>e.checked).map(([k])=>k)}:{})});await load();renderStage();
      }));root.append(box);
    });
  }
  function renderStoryMap(root){
    const panel=node("div",null,"panel story-map"),stories=p.stories.filter(s=>!s.redacted),journeys=[...new Set(stories.map(s=>s.journey||"미분류"))],releases=[...new Set(stories.map(s=>s.release||"미정"))];
    panel.append(node("h2","스토리 맵 · 여정 × 릴리스"));const table=node("table"),head=node("tr");head.append(node("th","릴리스"));journeys.forEach(j=>head.append(node("th",j)));table.append(head);
    for(const release of releases){const tr=node("tr");tr.append(node("th",release));for(const journey of journeys){const td=node("td");stories.filter(s=>(s.release||"미정")===release&&(s.journey||"미분류")===journey).forEach(s=>td.append(planningButton(s.title+(s.mvp?" · MVP":""),()=>{p.story=s;renderStage();})));tr.append(td);}table.append(tr);}panel.append(table);root.prepend(panel);
  }
  function documentEditor(root){
    const rows=p.documents.filter(d=>!d.redacted&&d.stage===p.stage),toolbar=node("div",null,"planning-toolbar");root.append(toolbar);
    toolbar.append(planningButton("새 문서 직접 작성",()=>{p.document=null;renderStage();}),planningButton("AI초안작성",()=>prompt(stages[p.stage]+" · AI초안작성",async intent=>{
      p.document=await api("/api/definitions/generate",{stage:p.stage,prompt:intent});await load();renderStage();
    })));
    const list=node("div",null,"planning-list");rows.forEach(d=>list.append(planningButton(d.title+" · v"+d.version+" · "+d.state,()=>{p.document=d;renderStage();})));root.append(list);
    const row=p.document?.stage===p.stage?p.document:{},form=node("form",null,"panel");root.append(form);
    const title=pf(form,"document-title","문서 제목",row.title||stages[p.stage],"text");title.required=true;
    const sections=repeat(form,"문단",row.sections||[{id:"overview",title:stages[p.stage],text:"",evidence_ids:[]}],[["id","문단 식별자","text"],["title","문단 제목","text"],["text","본문","textarea"]]);
    const assumptions=pf(form,"document-assumptions","가정 · 한 줄에 하나",(row.assumptions||[]).join("\n"));
    const questions=repeat(form,"문서 확인 질문",row.questions||[],[["text","질문","textarea"],["status","상태","select",[["unanswered","미해소"],["answered","답변 완료"],["excluded","범위 제외"]]],["answer","답변·제외 사유","textarea"]]);
    const stories=p.stories.filter(s=>!s.redacted&&s.definition_status==="confirmed"),storySelect=pick(form,"document-stories","연결할 확정 스토리",stories.map(s=>[s.id,s.title+" · v"+s.version]),(row.story_refs||[]).map(s=>s.id),true);
    const save=node("button","문서 초안 저장","primary");save.type="submit";form.append(save);
    form.onsubmit=planningGuard(async()=>{p.document=await api(row.id?"/api/definitions/update":"/api/definitions",{stage:p.stage,title:title.value,sections:sections().map(s=>({...s,evidence_ids:s.evidence_ids||[]})),assumptions:lines(assumptions),questions:questions(),story_refs:stories.filter(s=>values(storySelect).includes(s.id)).map(s=>({id:s.id,version:s.version})),...(row.id?{document_id:row.id,expected_version:row.version}:{})});await load();renderStage();notice("문서 초안을 저장했습니다.");});
    if(row.id){const controls=node("div",null,"panel");root.append(controls,node("p",row.id+" · v"+row.version+" · "+row.state));
      controls.append(planningButton("이 버전을 기준 문서로 확정",async()=>{p.document=await api("/api/definitions/confirm",{document_id:row.id,expected_version:row.version});await load();renderStage();notice("검토한 문서를 후속 기획의 기준으로 선택했습니다.");}));
      if(row.state==="confirmed")exportButtons(controls,"/api/definitions/export",{document_id:row.id,expected_version:row.version},row.id+"-v"+row.version);
    }
    const inputs=node("details",null,"panel");inputs.append(node("summary","후속 기획에 사용되는 검토 리서치"));p.results.filter(r=>!r.redacted&&r.state==="reviewed").forEach(r=>inputs.append(node("p",r.title+" · "+r.category+" · v"+r.version)));root.append(inputs);
  }
  function renderStage(){
    const root=$("definition-workspace"),tabs=$("definition-tabs");root.replaceChildren();tabs.replaceChildren();
    for(const [key,title] of Object.entries(stages)){const tab=planningButton(title,()=>{p.stage=key;p.document=null;renderStage();});tab.classList.toggle("active",key===p.stage);tabs.append(tab);}
    if(p.stage==="stories")storyEditor(root);else documentEditor(root);
  }
  function resultCards(root,category){
    for(const row of p.results.filter(r=>!r.redacted&&r.category===category)){
      const card=node("details",null,"planning-entry");card.append(node("summary",row.title+" · v"+row.version+" · "+row.state));
      const text=pf(card,"research-review-text","분석 내용·조건·한계",row.text);
      const actual=row.category==="fgi_actual"?pf(card,"actual-report","실제 고객 조사 보고서임을 확인했습니다 · 가상 FGI 제외",row.actual_customer_data,"checkbox"):null;
      card.append(planningButton("검토본 저장 · PRD 작성에 연결",async()=>{await api("/api/research-results/review",{result_id:row.id,expected_version:row.version,text:text.value,...(actual?{actual_customer_data:actual.checked}:{})});await load();await window.planningPage(category==="existing_service"?"baseline":category==="fgi_actual"?"studies":category==="voc"?"voc":"chat");notice("검토한 리서치 결과를 후속 기획에서 사용할 수 있습니다.");}),
        planningButton("분석 Markdown 내보내기",()=>download(row.id+".md",row.text)));root.append(card);
    }
  }
  async function baseline(){
    const root=$("baseline-workspace");root.replaceChildren(node("h2","기존 서비스 PRD·매뉴얼"));
    uploadForm(root,"existing_service",baseline);
    const assets=p.assets.filter(a=>a.purpose==="existing_service"&&a.media_type==="document"),selected=pick(root,"baseline-assets","함께 분석할 문서",assets.map(a=>[a.id,a.title+" · v"+a.version]),[],true);
    root.append(planningButton("AI초안작성 · 전체 서비스 분석",()=>prompt("기존 서비스 종합 분석",async intent=>{
      await api("/api/service-analysis",{prompt:intent,asset_refs:assets.filter(a=>values(selected).includes(a.id)).map(a=>({id:a.id,version:a.version}))});await load();await baseline();
    })));resultCards(root,"existing_service");
  }
  function saveAnalysisForm(root,category){
    root.append(node("h2",category==="fgi_actual"?"실제 FGI 결과 등록":"분석 결과를 기획 지식으로 저장"));
    if(category==="fgi_actual")uploadForm(root,"actual_fgi",async()=>window.planningPage("studies"));
    const form=node("form"),title=pf(form,"result-title","분석 제목","","text"),text=pf(form,"result-text","분석 내용·조건·한계");title.required=true;text.required=true;
    let assetSelect,messageSelect,conv=state.conversation;
    if(category==="fgi_actual")assetSelect=pick(form,"fgi-source","실제 FGI 결과 문서",[["","선택하세요"],...p.assets.filter(a=>a.purpose==="actual_fgi").map(a=>[a.id,a.title+" · v"+a.version])]);
    else {
      root.append(planningButton("분석 대화 열기",()=>page("chat")));
      messageSelect=pick(form,"analysis-messages","현재 대화에서 연결할 근거 있는 답변",(conv?.messages||[]).filter(m=>!m.redacted&&m.evidence_ids?.length).map(m=>[m.id,m.text.slice(0,100)]),[],true);
      if(!conv)form.append(node("p","먼저 분석 대화를 열고 질문한 후 이 화면으로 돌아오세요."));
    }
    const save=node("button","검토 전 결과 저장","primary");save.type="submit";form.append(save);root.append(form);
    form.onsubmit=planningGuard(async()=>{
      const asset=p.assets.find(a=>a.id===assetSelect?.value);
      await api("/api/research-results",{category,title:title.value,text:text.value,...(asset?{asset_id:asset.id,asset_version:asset.version}:{conversation_id:conv?.id,message_ids:messageSelect?values(messageSelect):[]})});
      await load();await window.planningPage(category==="fgi_actual"?"studies":category==="voc"?"voc":"chat");notice("분석 초안을 저장했습니다. 내용을 검토한 후 후속 기획에 연결하세요.");
    });
    resultCards(root,category);
  }
  async function catalog(){
    const root=$("persona-catalog-workspace");root.replaceChildren(node("h2","가상 페르소나 설계 카탈로그"),node("p","32개 설계 프로필에서 검색·선택해 근거를 연결하세요. 실제 고객이나 AI가 생성한 관찰 결과가 아닙니다."));
    const search=pf(root,"catalog-query","역할·목표·제약 키워드","","text"),list=node("div");root.append(list);
    async function results(){const data=await api("/api/persona-catalog?q="+encodeURIComponent(search.value));list.replaceChildren();
      const selected=pick(list,"catalog-profiles","등록할 설계 프로필",data.catalog.filter(c=>!c.registered_id).map(c=>[c.catalog_key,c.name+" · "+c.segment+" · "+c.goals]),[],true);
      const evidence=pick(list,"catalog-evidence","프로필에 연결할 현재 근거",state.evidence.map(e=>[e.id,(e.title||e.text).slice(0,70)]),[],true);
      list.append(node("p",`정의된 프로필 ${data.defined_count}개 · 활성 풀 상한 ${data.active_limit}명`),planningButton("선택 프로필 등록",async()=>{
        await api("/api/persona-catalog/register",{catalog_keys:values(selected),evidence_ids:values(evidence)});await refresh();await results();notice("가상 설계 프로필을 등록했습니다. 가정과 실제 근거를 검토하세요.");
      }));
    }
    root.insertBefore(planningButton("카탈로그 검색",results),list);await results();
  }
  function publicResearch(){
    const host=$("public-research-workspace"),root=node("details");root.append(node("summary","공개 웹 선진사례 조사 · 분석 지식 저장"));host.replaceChildren(root);
    const form=node("form"),query=pf(form,"web-query","공개 자료 검색어","","text"),out=node("div");query.required=true;
    const submit=node("button","공개 웹 검색","secondary");submit.type="submit";form.append(submit);root.append(form,out);
    form.onsubmit=planningGuard(async()=>{const result=await api("/api/public-research",{query:query.value});out.replaceChildren(node("p",result.disclosure));
      const selected=pick(out,"web-candidates","비교 분석할 검색 후보",result.results.map(r=>[r.id,r.title]),[],true);
      out.append(planningButton("AI초안작성 · 공개 사례 비교",()=>prompt("공개 웹 사례 비교 분석",async intent=>{await api("/api/public-research/analyze",{search_id:result.search_id,expected_version:result.version,candidate_ids:values(selected),prompt:intent});await load();publicResearch();})));
      result.results.forEach(r=>{const card=node("div",null,"planning-entry"),a=node("a",r.title);a.href=r.url;a.target="_blank";a.rel="noopener noreferrer";card.append(a,node("p",r.text));out.append(card);});
    });
    const analysis=node("details");analysis.append(node("summary","선진사례 채팅 분석을 후속 기획에 연결"));root.append(analysis);saveAnalysisForm(analysis,"benchmark");
  }
  window.planningPage=async name=>{
    const stage=["definition","prototype","uat"].includes(name)?name:"research";
    document.querySelectorAll("[data-stage]").forEach(b=>b.classList.toggle("active",b.dataset.stage===stage));
    if(["baseline","definition","voc","studies","personas","chat"].includes(name))await load();
    if(name==="definition")renderStage();if(name==="baseline")await baseline();if(name==="personas")await catalog();if(name==="chat")publicResearch();
    if(name==="voc"){const root=$("voc-result-workspace");root.replaceChildren();saveAnalysisForm(root,"voc");}
    if(name==="studies"){const root=$("actual-fgi-workspace");root.replaceChildren();root.append(planningButton("페르소나 검색·풀 관리",()=>page("personas")));saveAnalysisForm(root,"fgi_actual");}
  };
  window.planningReset=()=>{
    closePrompt();canvasView=null;editorFields={};p={stage:"product",assets:[],stories:[],documents:[],results:[],drafts:[],runs:{},extractions:[],selectedRuns:[],recovery:null,story:null,document:null,activeAsset:null,activeRun:null};
    for(const id of ["baseline-workspace","definition-workspace","public-research-workspace","voc-result-workspace","actual-fgi-workspace","persona-catalog-workspace"])$(id).replaceChildren();
  };
  for(const name of ["research","personas","prd"]){const b=document.querySelector(`[data-page="${name}"]`);if(b)$("planning-tools").append(b);}
  document.querySelectorAll("[data-stage]").forEach(b=>b.onclick=planningGuard(()=>page(b.dataset.stage==="research"?"baseline":b.dataset.stage)));
})();
