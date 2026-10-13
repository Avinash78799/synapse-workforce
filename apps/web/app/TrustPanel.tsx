'use client';
import {useEffect,useState} from 'react';
type Api=(path:string,method?:string,body?:unknown)=>Promise<any>;
type Action={id:number;timestamp:string;agent:string;summary:string;reason:string;policy_version:string;permission:string;status:string;environment:string;reversibility:string};
export function TrustPanel({api,onApprovals}:{api:Api;onApprovals:()=>void}){
 const [data,setData]=useState<{pending_approvals:number;actions:Action[];notice:string}|null>(null),[error,setError]=useState('');
 useEffect(()=>{let current=true;let timer:ReturnType<typeof setTimeout>;const load=async()=>{try{const value=await api('/trust');if(current){setData(value);setError('')}}catch{if(current)setError('Could not refresh action history. Any displayed information may be out of date.')}finally{if(current)timer=setTimeout(load,5000)}};void load();return()=>{current=false;clearTimeout(timer)}},[api]);
 return <div className="client-area"><section className="panel"><h2>Your decisions. A clear record.</h2><p>See what your support assistant proposed, why it was allowed or stopped, and what happened next.</p>{data&&<><p><strong>{data.pending_approvals}</strong> actions waiting for approval</p><button className="button primary" onClick={onApprovals}>Review pending approvals</button><p className="subtle">{data.notice}</p></>}</section>
 {error&&<p role="alert" className="banner error">{error}</p>}{!data&&!error&&<p role="status">Loading action history…</p>}
 {data?.actions.length===0&&<section className="panel"><h3>No recorded actions yet</h3><p>Once a workflow runs, its proposed actions and outcomes will appear here.</p></section>}
 {data?.actions.map(a=><article className="panel" key={a.id}><div className="panel-heading"><h3>{a.summary}</h3><time dateTime={a.timestamp}>{new Date(a.timestamp).toLocaleString()}</time></div><p>{a.agent} · {a.environment}</p><dl className="trust-facts"><dt>Why</dt><dd>{a.reason}</dd><dt>Permission</dt><dd>{a.permission}</dd><dt>Current status</dt><dd>{a.status}</dd><dt>Can it be reversed?</dt><dd>{a.reversibility}</dd><dt>Policy used</dt><dd>Version {a.policy_version}</dd></dl></article>)}
 </div>
}
