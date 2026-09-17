'use client';
import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ShieldAlert, RefreshCw } from 'lucide-react';

type Api=(path:string,method?:string,body?:unknown)=>Promise<any>; // Backend contracts are validated at the API boundary.
type Frozen={id:string;run_id:string;agent_name:string;tool:string;arguments:Record<string,unknown>;result:unknown;updated_at:string;idempotency_key:string};
type Budget={day_utc:string;provider:string;daily_limit_usd:number;reserved_usd:number;remaining_usd:number;recorded_cost_usd:string;uncertain_runs:number;basis:string;runs:{id:string;objective:string;provider:string;cost_usd:string|null;input_tokens:number;output_tokens:number}[]};
const usd=(n:number|string)=>'$'+Number(n).toFixed(4);

export function ReconciliationPanel({api,role,openRun}:{api:Api;role:string;openRun:(id:string)=>void}){
 const [rows,setRows]=useState<Frozen[]|null>(null),[reasons,setReasons]=useState<Record<string,string>>({}),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const refresh=useCallback(()=>api('/reconciliation').then(setRows).catch(e=>setError(e.message)),[api]);
 useEffect(()=>{refresh();const timer=setInterval(refresh,5000);return()=>clearInterval(timer)},[refresh]);
 const resolve=async(row:Frozen,choice:string)=>{setBusy(true);setError('');setNotice('');try{await api('/reconciliation/'+row.id+'/resolve','POST',{choice,reason:reasons[row.id]});setNotice(choice==='verify'?'Provider evidence verified. The run remains closed for human follow-up.':'Case closed as unresolved. No action was retried.');await refresh()}catch(e){setError(e instanceof Error?e.message:'Review failed')}finally{setBusy(false)}};
 return <div className="approval-list"><section className="panel control-intro"><ShieldAlert/><div><h2>Review frozen actions</h2><p>UNKNOWN means the action’s outcome is uncertain. Check recorded evidence before deciding. Reviews never retry a refund or send a customer message.</p></div><button className="button secondary" onClick={refresh}><RefreshCw size={16}/>Refresh</button></section>
 {error&&<div className="banner error" role="alert">{error}</div>}{notice&&<div className="banner success" role="status">{notice}</div>}
 {rows===null?<p>Loading actions…</p>:rows.length===0?<section className="panel"><div className="empty"><CheckCircle2/><h3>No frozen actions</h3><p>Actions with uncertain outcomes will appear here for review.</p></div></section>:rows.map(row=><section key={row.id} className="panel approval-card"><div className="approval-header"><div><h2>{row.tool}</h2><p>{row.agent_name} · {new Date(row.updated_at).toLocaleString()}</p></div><span className="badge failed">Unknown</span></div><details><summary>Inspect original action and evidence</summary><pre className="review-evidence">{JSON.stringify({arguments:row.arguments,receipt:row.result,idempotency_key:row.idempotency_key},null,2)}</pre></details><button className="text-button" onClick={()=>openRun(row.run_id)}>Inspect run</button>{['owner','finance_approver'].includes(role)?<div className="reconciliation-form"><label>Review reason<textarea minLength={10} maxLength={500} rows={2} value={reasons[row.id]||''} onChange={e=>setReasons({...reasons,[row.id]:e.target.value})}/></label><div className="filters"><button className="button primary" disabled={busy||(reasons[row.id]||'').trim().length<10} onClick={()=>resolve(row,'verify')}>Verify provider evidence</button><button className="button secondary" disabled={busy||(reasons[row.id]||'').trim().length<10} onClick={()=>resolve(row,'close_unresolved')}>Close unresolved</button></div><p>Neither choice resumes the run. Unresolved actions remain recorded as failures.</p></div>:<p className="role-note">An owner or finance approver must record the review.</p>}</section>)}</div>
}

export function BudgetPanel({api}:{api:Api}){
 const [budget,setBudget]=useState<Budget|null>(null),[error,setError]=useState('');
 useEffect(()=>{let active=true;const load=()=>api('/budgets').then(v=>{if(active)setBudget(v)}).catch(e=>{if(active)setError(e.message)});load();const timer=setInterval(load,5000);return()=>{active=false;clearInterval(timer)}},[api]);
 if(error)return <div className="banner error" role="alert">{error}</div>;
 if(!budget)return <p>Loading model usage…</p>;
 return <><section className="panel control-intro"><div><h2>Model spend and dispatch budget</h2><p>{budget.provider==='simulation'?'Simulation mode: no live model calls or paid usage.':'Configured provider usage; reconcile with provider invoices.'} Daily budget resets at midnight UTC ({budget.day_utc}).</p></div></section><div className="metrics-grid budget-metrics">{[['Daily limit',usd(budget.daily_limit_usd)],['Reserved capacity',usd(budget.reserved_usd)],['Recorded run cost',usd(budget.recorded_cost_usd)],['Available capacity',usd(budget.remaining_usd)]].map(([label,value])=><div className="metric" key={label}><div>{label}</div><strong>{value}</strong></div>)}</div><p className="control-note">{budget.basis} {budget.uncertain_runs>0?`${budget.uncertain_runs} run(s) have unknown or unreported costs.`:''}</p><section className="panel"><div className="panel-heading"><h2>Recent runs</h2><span className="subtle">Latest 50 · All dates</span></div><div className="table-scroll"><table><thead><tr><th>Objective</th><th>Provider</th><th>Tokens</th><th>Recorded cost</th></tr></thead><tbody>{budget.runs.map(r=><tr key={r.id}><td>{r.objective}</td><td>{r.provider}</td><td>{r.input_tokens+r.output_tokens}</td><td>{r.cost_usd===null?'Not recorded':usd(r.cost_usd)}</td></tr>)}</tbody></table></div>{budget.runs.length===0&&<div className="empty"><h3>No run usage yet</h3></div>}</section></>
}

export function ApprovalSignals({api,role}:{api:Api;role:string}){
 const [rows,setRows]=useState<{id:number;actor:string;details:{signal:string};created_at:string}[]>([]),[error,setError]=useState('');
 useEffect(()=>{if(!['owner','finance_approver'].includes(role))return;let active=true;const load=()=>api('/security/signals').then(v=>{if(active)setRows(v)}).catch(e=>{if(active)setError(e.message)});load();const timer=setInterval(load,5000);return()=>{active=false;clearInterval(timer)}},[api,role]);
 if(error)return <div className="banner error">{error}</div>;
 if(!rows.length)return null;
 return <section className="panel"><div className="panel-heading"><h2>Approval security signals</h2><span className="subtle">Latest 50 · Repeated signals grouped for 60 seconds</span></div><div className="table-scroll"><table><thead><tr><th>Signal</th><th>Actor</th><th>Time</th></tr></thead><tbody>{rows.map(r=><tr key={r.id}><td>{r.details.signal.replaceAll('_',' ')}</td><td className="mono">{r.actor.slice(0,8)}</td><td>{new Date(r.created_at).toLocaleString()}</td></tr>)}</tbody></table></div></section>
}

export function PrivacyPanel({api,role}:{api:Api;role:string}){
 const [principal,setPrincipal]=useState(''),[purpose,setPurpose]=useState('support.order_resolution'),[evidence,setEvidence]=useState(''),[affirmed,setAffirmed]=useState(false),[rows,setRows]=useState<any[]>([]),[message,setMessage]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[confirmErase,setConfirmErase]=useState(false);
 const allowed=['owner','operator'].includes(role);
 const run=async(action:string)=>{setBusy(true);setError('');setMessage('');try{
   const body={data_principal_id:principal.trim()};
   if(action==='load'){const r=await api('/privacy/consent/'+encodeURIComponent(principal.trim()));setRows(r.consents)}
   if(action==='grant'){await api('/privacy/consent','POST',{...body,purpose,notice_version:'1.0',metadata:{evidence_reference:evidence.trim(),operator_attestation:true}});setMessage('Consent recorded for this purpose.');setAffirmed(false)}
   if(action==='withdraw'){await api('/privacy/consent/withdraw','POST',{...body,purpose});setMessage('Consent withdrawn. New dispatches for this purpose are blocked. Already accepted external actions require reconciliation.')}
   if(action==='export'){const r=await api('/privacy/dsar','POST',body);const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='privacy-export.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);setMessage('Operational export downloaded. Processor disclosures still require review.')}
   if(action==='erase'){const r=await api('/privacy/erase','POST',body);setMessage(r.notice);setConfirmErase(false)}
   if(action!=='load'){const r=await api('/privacy/consent/'+encodeURIComponent(principal.trim()));setRows(r.consents)}
 }catch(e){setError(e instanceof Error?e.message:'Privacy request failed')}finally{setBusy(false)}};
 if(!allowed)return <section className="panel"><h2>Privacy controls</h2><p>An owner or operator manages customer privacy requests.</p></section>;
 return <section className="panel"><h2>Customer privacy controls</h2><p>Record consent only after verifying the customer’s request and the applicable notice. Withdrawing consent blocks future workflow actions.</p>
 <label>Customer reference<input value={principal} maxLength={100} onChange={e=>{setPrincipal(e.target.value);setRows([]);setConfirmErase(false)}} placeholder="CUS-001"/></label>
 <label>Purpose<select value={purpose} onChange={e=>setPurpose(e.target.value)}><option value="support.order_resolution">Support: order resolution</option><option value="operations.fulfillment">Operations: fulfillment</option></select></label>
 <label>Consent evidence reference<input value={evidence} maxLength={300} onChange={e=>setEvidence(e.target.value)} placeholder="Verified request or consent receipt reference"/></label>
 <label><input type="checkbox" checked={affirmed} onChange={e=>setAffirmed(e.target.checked)}/> I verified affirmative consent for this purpose and notice.</label>
 <div className="filters"><button className="button secondary" disabled={busy||!principal.trim()} onClick={()=>run('load')}>View consent</button><button className="button primary" disabled={busy||!principal.trim()||!affirmed||evidence.trim().length<3} onClick={()=>run('grant')}>Record consent</button><button className="button secondary" disabled={busy||!principal.trim()} onClick={()=>run('withdraw')}>Withdraw consent</button><button className="button secondary" disabled={busy||!principal.trim()} onClick={()=>run('export')}>Export data</button></div>
 {error&&<p role="alert" className="banner error">{error}</p>}{message&&<p role="status" className="banner success">{message}</p>}
 <ul>{rows.map((r,i)=><li key={r.id||i}>{r.purpose}: {r.status}</li>)}</ul>
 <h3>Erase operational data</h3><p>This removes matched memories and knowledge documents and scrubs customer details. Active runs must be finished or aborted first. Financial evidence, audit history, backups and external copies remain subject to retention review.</p>
 <label><input type="checkbox" checked={confirmErase} onChange={e=>setConfirmErase(e.target.checked)}/> I verified this erasure request and understand the operational changes cannot be undone.</label>
 <button className="button secondary" disabled={busy||!principal.trim()||!confirmErase} onClick={()=>run('erase')}>Scrub operational data</button>
 </section>
}
