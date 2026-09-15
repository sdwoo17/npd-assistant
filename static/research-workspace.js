"use strict";
// All rendering uses text nodes. Scope and versions are checked by the server.
(() => {
  let data=null, sources=[], researchId="", editing=null, lastExport=null, serial=0;
  const locks=new WeakMap();
  async function action(fn){
    const controls=[...$("research-workspace").querySelectorAll("input,textarea,select,button")];
    for(const e of controls){const l=locks.get(e)||{n:0,disabled:e.disabled};l.n++;locks.set(e,l);e.disabled=true;}
    try{return await fn();}finally{for(const e of controls){const l=locks.get(e);if(--l.n===0){e.disabled=l.disabled;locks.delete(e);}}}
  }
  const b=(label,fn,cls)=>button(label,()=>action(fn),cls);
  const typeNames={po_interview:"PO 인터뷰",calculation:"지표 합산 계산",funnel:"누적 퍼널",voc_coding:"VoC 코딩 집계",evidence:"근거 카드",conflict:"출처 충돌",numeric_claim:"수치 주장",metric:"지표 정의",problem:"문제·인사이트",story_candidate:"스토리 후보",requirement:"요구사항",acceptance:"인수 기준",uat:"UAT 과업",question:"미결 질문",utility:"작성 효과 측정",input_policy:"입력 상한 결정"};
  const translations={as_is:"현재 흐름",proposed:"제안 흐름",states:"상태와 전이",exceptions:"예외 처리",permissions:"권한",pain_points:"불편·문제",unknowns:"미확인 사항",roles:"역할",jobs:"사용자 과업",STRUCTURED:"상세 표 분석",LEGACY_TEXT:"기존 텍스트 분석",CURRENT:"현재 지원",RETIRED:"종료됨",PO_REPORTED:"PO가 직접 확인",DEFERRED:"보류",DOCUMENT_CONFIRMED:"문서 내용 확인",OPERATION_CHANGE:"최신 운영 변경",DOCUMENT_CORRECTION:"문서 오류 정정",EXCEPTION:"예외 상황",FUTURE_REQUEST:"향후 희망사항",RATIO_OF_SUMS:"합산 분자 / 합산 분모",PERCENT_OF_SUMS:"합산 분자 / 합산 분모 × 100",INCLUDED:"집계 포함",DUPLICATE:"중복 제외",REAL:"실제 발언",MIXED:"실제·합성 혼합",INTERNAL:"우리 제품 실측",EXTERNAL:"외부 사례 실측",CONFIRMED_CURRENT:"현재 구현 상한 확인",UNKNOWN:"미확인·미측정",OBSERVED:"실측",SYNTHETIC:"합성 예시",PROPOSED:"제안",PREDICTED:"예측",UNVERIFIED:"미검증",SUPPORTED:"지지됨",REFUTED:"반박됨",CONFLICTED:"충돌 있음",NOT_RUN:"미실행",EXECUTED:"실행 확인",PASS:"통과",FAIL:"실패",LEVEL:"수준",RELATIVE_CHANGE:"상대 증감",PERCENTAGE_POINT:"퍼센트포인트",MULTIPLE:"배수",SHARE:"비중",PUBLIC_FACT:"공개 자료의 사실 주장",ATTACHMENT_STATEMENT:"첨부 자료의 진술",INTERNAL_MEASUREMENT:"내부 실측",REAL_VOC:"실제 VoC",SYNTHETIC_FGI:"가상 FGI",ASSUMPTION:"가정",USER_REQUIREMENT:"사용자 요구",OPEN:"미결",ANSWERED:"답변 완료",EXCLUDED:"제외",RESEARCH:"연구 검토 전",DEVELOPMENT:"개발 착수 전",RELEASE:"출시 전",UNRESOLVED:"미해결",RESOLVED:"해결",SCOPED_OUT:"범위 제외",BLOCKING:"중요·개발 차단",NONBLOCKING:"후속 확인",SNIPPET_ONLY:"검색 요약문만 확인",PASSAGE_REVIEWED:"원문 구간 확인",DOCUMENTED:"문서로 확인",TESTED:"실행 시험으로 확인",UNSUPPORTED:"미지원",COMPARABLE:"조건 비교 가능",NOT_COMPARABLE:"직접 비교 불가",OBSERVATIONAL:"관찰 비교",RANDOMIZED:"무작위 배정",ADOPT:"채택",TEST:"시험",DEFER:"보류",EXCLUDE:"제외",INCONCLUSIVE:"판단 보류"};
  const typeTitle=kind=>Object.values(data.tasks).find(t=>t[1]===kind)?.[0]||typeNames[kind]||kind;
  function input(root,label,value="",type="text",options=[]){
    const id="research-field-"+(++serial),e=node(type==="select"?"select":type==="textarea"?"textarea":"input");e.id=id;
    const l=node("label",label);l.htmlFor=id;root.append(l,e);
    if(type==="select")for(const [v,t] of options){const o=node("option",t);o.value=v;e.append(o);}
    else if(type!=="textarea")e.type=type;
    if(type==="checkbox")e.checked=!!value;else e.value=value??"";
    return e;
  }
  function multi(root,label,options,chosen=[]){const e=input(root,label,"","select",options);e.multiple=true;for(const o of e.options)o.selected=chosen.includes(o.value);return e;}
  const values=e=>[...e.selectedOptions].map(o=>o.value);
  const ref=r=>({kind:r.kind,id:r.id,version:r.version});
  const label=r=>(r.title||r.text?.slice(0,45)||r.id)+" · v"+r.version;
  async function refresh(){
    const result=await Promise.all([api("/api/research-workspace"),api("/api/evidence"),api("/api/planning-assets"),api("/api/research-results"),api("/api/debriefs"),api("/api/stories"),api("/api/definitions")]);
    data=result[0];sources=[...result[1],...result[2],...result[3].filter(r=>r.state==="reviewed"),...result[4].filter(r=>r.review_status==="po_reviewed"),...result[5].filter(r=>r.definition_status==="confirmed"),...result[6].documents.filter(r=>r.state==="confirmed")].filter(r=>!r.redacted&&!r.withdrawn);
    if(researchId&&!data.items.some(r=>r.id===researchId&&!r.redacted))researchId="";
    if(editing?.id)editing=data.items.find(r=>r.id===editing.id&&!r.redacted)||null;
  }
  function download(name,value,type="application/json"){
    const url=URL.createObjectURL(new Blob([typeof value==="string"?value:JSON.stringify(value,null,2)],{type}));
    const a=node("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function showGate(root,result){
    root.replaceChildren(node("h3",result.ready?(result.stage==="development"?"개발 검토 준비 조건 충족":"연구 검토 준비 조건 충족"):"보완이 필요합니다"),node("p","문서 준비 상태이며 실제 고객 검증·개발 승인·UAT 성공을 뜻하지 않습니다."));
    for(const message of result.errors||[])root.append(node("p","보완: "+message,"error"));
    for(const message of result.warnings||[])root.append(node("p","확인: "+message));
    if(result.checks){const list=node("ul");for(const c of result.checks)list.append(node("li",(c.complete?"✓ ":"○ ")+c.task_id+" "+c.title));root.append(list);}
    if(result.graph?.length){const list=node("ul");for(const e of result.graph)list.append(node("li",e.from+" → "+e.to+" · "+e.reason));root.append(node("h3","근거와 검증 연결"),list);}
  }
  function fieldControl(root,label,spec,value){
    const type=["select","number","checkbox"].includes(spec.type)?spec.type:"textarea";
    const e=input(root,label,Array.isArray(value)?value.join("\n"):value??"",type,spec.type==="select"?[["","선택하세요"],...spec.options.map(v=>[v,translations[v]||v])]:[]);
    if(type==="number")e.step="any";return e;
  }
  function fieldValue(e,spec){return spec.type==="rows"?e.read():e.multiple?values(e):spec.type==="checkbox"?e.checked:spec.type==="number"?(e.value.trim()===""?null:Number(e.value)):spec.type==="lines"?e.value.split("\n").map(v=>v.trim()).filter(Boolean):e.value;}
  function rowsEditor(root,spec,entries){
    const box=node("fieldset"),legend=node("legend",spec.label);box.append(legend);root.append(box);const rows=[];
    function add(entry={}){
      if(rows.filter(r=>r.wrap.parentNode).length>=spec.max_rows)throw new Error("표는 100행까지 작성할 수 있습니다.");
      const wrap=node("details",null,"planning-entry");wrap.open=true;box.append(wrap);wrap.append(node("summary",spec.label+" · "+(entry.id||"새 행")));
      const cells={};for(const [key,col] of Object.entries(spec.columns))cells[key]=fieldControl(wrap,col.label,col,entry[key]);
      wrap.append(b("이 행 삭제",()=>wrap.remove()));rows.push({wrap,cells});
    }
    for(const entry of entries||[])add(entry);box.append(b(spec.label+" 행 추가",()=>add()));
    return {read:()=>rows.filter(r=>r.wrap.parentNode).map(r=>Object.fromEntries(Object.entries(spec.columns).map(([key,col])=>[key,fieldValue(r.cells[key],col)])))};
  }
  function calculated(root,row){
    if(row.evidence_nature)root.append(node("p","자료 성격: "+(translations[row.evidence_nature]||row.evidence_nature)));
    const c=row.computed;if(!c)return;
    const box=node("section",null,"panel");box.append(node("h3","저장된 입력의 계산 결과"));root.append(box);
    if(["INVALID","UNKNOWN","NOT_COMPUTABLE"].includes(c.status))box.append(node("p",c.reason||"계산 입력을 확인하세요."));
    const n=v=>v==null?"산출 불가·미측정":String(v);
    if(row.output_type==="calculation")box.append(node("p",`합산 분자 ${n(c.numerator_sum)} / 합산 분모 ${n(c.denominator_sum)} · 결과 ${n(c.value)} ${c.unit||""}`),node("p",c.reason),node("p","개별 비율의 평균을 사용하지 않습니다. 인과적 개선 효과를 입증하지 않습니다."));
    if(row.output_type==="funnel")for(const step of c.steps||[])box.append(node("p",`${step.id}: ${n(step.count)}명 · 시작 대비 ${n(step.of_start_percent)}% · 이전 단계 대비 ${n(step.of_previous_percent)}% · 이탈 ${n(step.dropoff_from_previous)}명`));
    if(row.output_type==="voc_coding"){
      box.append(node("p",`포함 발언 ${c.included_utterances} · 실제 ${c.real_utterances} · 합성 ${c.synthetic_utterances} · 독립 참여자 ${n(c.independent_participants)} · 제외 ${c.excluded} · 중복 ${c.duplicates}`));
      for(const [code,count] of Object.entries(c.code_counts))box.append(node("p",code+": "+count));
      box.append(node("p","발언 수와 독립 참여자 수를 구분합니다. 전체 고객의 빈도·비율은 입증하지 않습니다."));
    }
  }
  function interviewActions(root,row){
    if(row.state!=="reviewed")return;
    if(row.output_type==="service_baseline")root.append(b("미결 질문으로 PO 인터뷰 준비",async()=>{editing=await api("/api/research-workspace/interview/start",{baseline_id:row.id,expected_version:row.version,title:row.title+" · PO 인터뷰"});await refresh();render();}));
    if(row.output_type!=="po_interview")return;
    const refs=(row.input_refs||[]).filter(r=>r.kind==="research_item"&&data.items.some(i=>i.id===r.id&&i.output_type==="service_baseline"));
    const box=node("section",null,"panel");root.append(box);box.append(node("h3","확인한 답변을 서비스 개정안에 반영"),node("p","선택한 답변으로 별도 초안을 만듭니다. 현재 기능·권한·상태를 바꾸면 관련 표를 다시 작성하고 검토해야 합니다."));
    const baseline=input(box,"대조한 서비스 버전",refs[0]?.id||"","select",refs.map(r=>[r.id,label(data.items.find(i=>i.id===r.id))]));
    const selected=multi(box,"반영할 확인 답변",row.fields.answers.filter(a=>a.status==="ANSWERED").map(a=>[a.id,a.id+" · "+a.question]));
    const title=input(box,"서비스 개정안 제목",row.title+" · 반영안"),reason=input(box,"반영 이유","","textarea");
    box.append(b("선택한 답변으로 별도 개정안 작성",async()=>{const origin=refs.find(r=>r.id===baseline.value);if(!origin)throw new Error("대조한 서비스 버전을 선택하세요.");editing=await api("/api/research-workspace/interview/apply",{interview_id:row.id,expected_version:row.version,baseline_id:origin.id,baseline_version:origin.version,answer_ids:values(selected),title:title.value,reason:reason.value});await refresh();render();}));
  }
  function itemEditor(root,row){
    const kind=row.output_type,form=node("form",null,"panel");root.append(form);
    form.append(node("h2",typeTitle(kind)+(row.id?" · v"+row.version:" · 새 초안")),node("p",row.id?"현재 상태: "+(row.state==="reviewed"?"검토 완료":"초안"):"저장 후 누락 항목을 보완하고 검토하세요."));
    if(row.conflict_ids?.length)form.append(node("p","연결된 충돌 기록: "+row.conflict_ids.join(", ")+" · "+row.effective_claim_status));
    form.append(b("편집 닫기",()=>{editing=null;render();}));
    const title=input(form,"산출물 제목",row.title||""),applicable=input(form,"적용 여부",row.applicability||"applicable","select",[["applicable","적용"],["not_applicable","해당 없음"]]),na=input(form,"해당 없음의 이유",row.na_reason||"","textarea");
    calculated(form,row);
    const controls={};
    for(const [key,spec] of Object.entries(data.types[kind])){
      let value=row.fields?.[key]??"";
      if(kind==="brief"&&key==="required_tasks"){
        controls[key]=multi(form,spec.label,Object.entries(data.tasks).map(([id,t])=>[id,id+" "+t[0]]),row.fields?.required_tasks||Object.keys(data.tasks));continue;
      }
      if(spec.type==="rows"){controls[key]=rowsEditor(form,spec,row.fields?.[key]);continue;}
      if(kind==="service_baseline"&&key==="detail_level"&&!value)value="STRUCTURED";
      controls[key]=fieldControl(form,spec.label+(spec.required?" · 검토 시 필수":""),spec,value);
    }
    const candidates=[...sources,...data.items.filter(r=>!r.redacted&&r.state==="reviewed"&&r.research_id===researchId&&r.id!==row.id&&r.output_type!=="brief")];
    const selected=multi(form,"참조할 검토 자료·원본",candidates.map(r=>[r.id,label(r)]),(row.input_refs||[]).map(r=>r.id));
    const linkBox=node("details");linkBox.open=true;linkBox.append(node("summary","근거·스토리·검증 관계 연결"));form.append(linkBox);const linkRows=[];
    function addLink(link={}){
      const wrap=node("div",null,"planning-entry");linkBox.append(wrap);
      const targets=data.items.filter(r=>!r.redacted&&r.state==="reviewed"&&r.research_id===researchId&&r.id!==row.id&&r.output_type!=="brief");
      const target=input(wrap,"근거·검증 대상",link.id||"","select",[["","선택하세요"],...targets.map(r=>[r.id,typeTitle(r.output_type)+" · "+label(r)])]);
      const relation=input(wrap,"이 산출물과의 관계",link.relation||"derived_from","select",[["supports","지지 근거"],["refutes","반대 근거"],["derived_from","도출 근거"],["validates","검증 대상"],["measures","측정 대상"]]);
      const reason=input(wrap,"의미·적용 조건이 연결되는 이유",link.reason||"","textarea");
      wrap.append(b("연결 삭제",()=>wrap.remove()));linkRows.push({wrap,target,relation,reason,targets});
    }
    for(const link of row.links||[])addLink(link);linkBox.append(b("관계 연결 추가",()=>addLink()));
    const reason=input(form,"변경·검토 이유",row.change_reason||"","textarea");
    function payload(){
      const fields={};for(const [key,spec] of Object.entries(data.types[kind])){
        fields[key]=fieldValue(controls[key],spec);
      }
      return {output_type:kind,task_id:row.task_id||"",research_id:researchId,title:title.value,fields,
        applicability:applicable.value,na_reason:na.value,change_reason:reason.value,
        input_refs:candidates.filter(r=>values(selected).includes(r.id)).map(ref),
        links:linkRows.filter(l=>l.wrap.parentNode&&l.target.value).map(l=>({...ref(l.targets.find(r=>r.id===l.target.value)),relation:l.relation.value,reason:l.reason.value})),
        ...(row.id?{item_id:row.id,expected_version:row.version}:{})};
    }
    const submit=node("button","초안 저장","primary");submit.type="submit";form.append(submit);
    form.onsubmit=guard(()=>action(async()=>{editing=await api("/api/research-workspace/items",payload());researchId=editing.research_id;await refresh();render();notice("초안을 저장했습니다. 검토 조건을 확인하세요.");}));
    if(row.id){
      const history=node("div");form.append(b("버전 이력 보기",async()=>{const rows=await api("/api/research-workspace/history/"+row.id);history.replaceChildren();for(const old of rows)history.append(node("pre",old.redacted?"원본 변경으로 비공개 처리된 버전":`v${old.version} · ${old.change_reason||""}\n${old.text}`));}),history);
      if(row.interview_application){const audit=node("details");audit.append(node("summary","PO 답변 반영 내역"));for(const change of row.interview_application.changes)audit.append(node("p",`${change.question_id} · ${translations[change.classification]||change.classification} · ${change.respondent_role} · 확인 ${change.confirmed_at} · 적용 ${change.effective_at}`),node("p","이전: "+change.before),node("p","반영: "+change.after));form.append(audit);}
      interviewActions(form,row);
      form.append(b("내용 저장 후 검토 완료",async()=>{if(!reason.value.trim())throw new Error("검토 이유를 입력하세요.");editing=await api("/api/research-workspace/items",payload());try{editing=await api("/api/research-workspace/items/review",{item_id:editing.id,expected_version:editing.version,reason:reason.value});}finally{await refresh();render();}notice("화면의 내용을 저장하고 해당 버전을 검토했습니다.");}),
        b("산출물 철회",async()=>{await api("/api/research-workspace/items/withdraw",{item_id:row.id,expected_version:row.version});editing=null;await refresh();render();}));
    }
    if(kind!=="brief"){
      const prompt=input(form,"AI 초안의 분석 질문","","textarea");
      form.append(b("선택한 근거로 AI 초안 작성",async()=>{editing=await api("/api/research-workspace/generate",{...payload(),prompt:prompt.value});await refresh();render();notice("별도 AI 초안을 저장했습니다. 실제 검증 상태는 확정되지 않았습니다.");}));
    }
  }
  function packsPanel(root){
    const panel=node("section",null,"panel");root.append(panel);panel.append(node("h2","PRD에 사용할 연구 묶음"));
    const available=data.items.filter(r=>!r.redacted&&r.research_id===researchId&&r.state==="reviewed");
    const title=input(panel,"묶음 제목",""),selected=multi(panel,"포함할 검토 산출물",available.map(r=>[r.id,typeTitle(r.output_type)+" · "+label(r)]));
    panel.append(b("검토 산출물 모두 선택",()=>{for(const o of selected.options)o.selected=true;}),b("선택한 버전으로 묶음 저장",async()=>{
      await api("/api/research-workspace/packs",{title:title.value,research_id:researchId,item_refs:available.filter(r=>values(selected).includes(r.id)).map(ref)});await refresh();render();
    }));
    for(const pack of data.packs.filter(r=>!r.redacted&&r.research_id===researchId)){
      const box=node("details");box.append(node("summary",label(pack)+(data.selection?.pack_id===pack.id?" · 선택됨":"")));panel.append(box);
      const results=node("div");box.append(b("PRD 작성에 이 버전 사용",async()=>{await api("/api/research-workspace/select",{pack_id:pack.id,expected_version:pack.version});await refresh();render();notice("이 연구 묶음을 후속 기획의 기준으로 선택했습니다.");}),
        b("연구 준비 검사",async()=>showGate(results,await api("/api/research-workspace/gate",{pack_id:pack.id,expected_version:pack.version}))),
        b("개발 전 확인 항목",async()=>showGate(results,await api("/api/research-workspace/gate",{pack_id:pack.id,expected_version:pack.version,stage:"development"}))),
        b("12영역 PRD 초안 구성",async()=>{await api("/api/research-workspace/compose",{pack_id:pack.id,expected_version:pack.version,title:pack.title+" · PRD 연구 초안"});await api("/api/research-workspace/select",{pack_id:pack.id,expected_version:pack.version});await page("definition");notice("PRD생성 단계에서 연구 초안을 선택해 검토하세요.");}));
      const changeReason=input(box,"인계 변경 이유","","textarea");
      const base=input(box,"이전 인계 ID · 첫 인계는 비워 두세요",lastExport?.export_id||"");
      box.append(b("연구 인계 JSON·Markdown 받기",async()=>{lastExport=await api("/api/research-workspace/export",{pack_id:pack.id,expected_version:pack.version,previous_export_id:base.value||undefined,reason:changeReason.value});download(lastExport.export_id+".json",lastExport.package);download(lastExport.export_id+".md",lastExport.markdown,"text/markdown");renderReceipt(results,lastExport);}));
      box.append(results);
    }
  }
  function renderReceipt(root,out){
    root.replaceChildren(node("h3","내보내기 완료 · 외부 수신 미확인"),node("p","실제 수신 자료가 있을 때 ID를 대조해 기록하세요. 이 화면은 외부 전송을 수행하지 않습니다."));
    const destination=input(root,"수신 대상",""),receipt=input(root,"수신 확인 자료·위치","","textarea"),mapping=[];
    for(const row of out.package.items)mapping.push({row,input:input(root,row.title+" · v"+row.version+"의 수신 ID","")});
    root.append(b("실제 수신 자료와 대조한 기록 저장",async()=>{await api("/api/research-workspace/receipt",{export_id:out.export_id,package_hash:out.hash,destination:destination.value,receipt_reference:receipt.value,
      mapping:mapping.map(m=>({source_id:m.row.id,source_version:m.row.version,destination_id:m.input.value}))});notice("PO 수신 대조 기록을 저장했습니다. 자동 연동 시험 결과와는 별도입니다.");}));
  }
  function render(){
    const root=$("research-workspace");root.replaceChildren(node("h2","조사 범위와 산출물"),node("p","조사 범위를 정하고 검토 산출물의 정확한 버전을 선택하세요. 모르는 값은 비워 두고 이유·후속 조사를 남깁니다."));
    const briefs=data.items.filter(r=>!r.redacted&&r.output_type==="brief");
    const scope=input(root,"조사 범위",researchId,"select",[["","선택하세요"],...briefs.map(r=>[r.id,label(r)])]);
    scope.onchange=()=>{researchId=scope.value;editing=null;render();};
    root.append(b("새 조사 브리프",()=>{editing={output_type:"brief",task_id:"RS-00",fields:{}};render();}),b("변경 영향 확인",async()=>{const r=await api("/api/research-workspace/impact");const out=node("div",null,"panel");out.append(node("p",r.action));for(const item of r.affected)out.append(node("p",item.id+" · v"+item.version+" · 재검토 필요"));root.append(out);}));
    if(researchId){
      const types=input(root,"작성할 산출물","","select",[["","선택하세요"],...Object.keys(data.types).filter(k=>k!=="brief").map(k=>[k,typeTitle(k)])]);
      root.append(b("새 산출물",()=>{if(!types.value)throw new Error("작성할 산출물을 선택하세요.");editing={output_type:types.value,task_id:Object.entries(data.tasks).find(([,t])=>t[1]===types.value)?.[0]||"",fields:{}};render();}));
      const baselines=sources.filter(r=>r.kind==="research_result"&&r.category==="existing_service");
      if(baselines.length){const origin=input(root,"상세 분석할 검토 서비스 문서",baselines[0].id,"select",baselines.map(r=>[r.id,label(r)])),intent=input(root,"상세 분석 질문","","textarea");root.append(b("검토 서비스 문서를 상세 표로 분석",async()=>{editing=await api("/api/research-workspace/generate",{research_id:researchId,output_type:"service_baseline",task_id:"SV-02",input_refs:[ref(baselines.find(r=>r.id===origin.value))],prompt:intent.value});await refresh();render();}));}
      const list=node("div",null,"planning-list");for(const row of data.items.filter(r=>!r.redacted&&r.research_id===researchId))list.append(b(label(row)+" · "+(row.state==="reviewed"?"검토 완료":"초안"),()=>{editing=row;render();}));root.append(list);
    }
    if(editing)itemEditor(root,editing);
    if(researchId)packsPanel(root);
    const c=data.input_capabilities;root.append(node("p",`현재 입력 상한: 파일당 ${c.file_bytes/1024/1024}MB · 스토리 생성의 원본 분석 ${c.story_extractions}개. ${c.reason}`));
  }
  window.researchWorkspacePage=async()=>{await refresh();render();};
  window.researchWorkspaceReset=()=>{data=null;sources=[];researchId="";editing=null;lastExport=null;$("research-workspace")?.replaceChildren();};
})();
