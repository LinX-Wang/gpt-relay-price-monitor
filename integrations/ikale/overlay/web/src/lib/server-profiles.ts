import type { StoredApiProfiles } from './config'
const endpoint='/detector/api/profiles',legacyKey='fingerpoint-detect-profiles-v1'
let state:StoredApiProfiles|null=null,revision=0,generation=0,savedGeneration=0,timer:ReturnType<typeof setTimeout>|undefined,flight:Promise<void>|null=null,blocked=false
const listeners=new Set<()=>void>()
const copy=<T,>(x:T):T=>structuredClone(x)
async function request(method='GET',body?:unknown){
 const r=await fetch(endpoint,{method,credentials:'same-origin',cache:'no-store',headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(15000)})
 if(!r.ok)throw Error(r.status===409?'其他电脑已修改配置。当前更改尚未同步，请先导出保留，再刷新后重试。':r.status===401?'登录已失效，请重新登录后保存配置。':'配置同步失败，请检查网络后重新点击保存。')
 return r.json()
}
function legacy():StoredApiProfiles|null {try{const v=JSON.parse(localStorage.getItem(legacyKey)||'null');return v&&Array.isArray(v.profiles)&&v.profiles.length&&v.savedProfiles?v:null}catch{return null}}
export async function initializeProfiles(){
 const remote=await request(),local=legacy();revision=remote.revision;state=remote.state
 if(local){
  if(!state)state=local
  else {
   const combined=copy(state)
   for(const p of local.profiles){
    if(combined.profiles.some(x=>JSON.stringify({...x,id:''})===JSON.stringify({...p,id:''})))continue
    const id=combined.profiles.some(x=>x.id===p.id)?crypto.randomUUID():p.id
    combined.profiles.push({...p,id});combined.savedProfiles[id]=local.savedProfiles[p.id]||{...p}
   }
   state=combined
  }
  const result=await request('PUT',{revision,state});revision=result.revision
  try{localStorage.removeItem(legacyKey)}catch{/* migration is already durable */}
 }
}
export function readServerProfiles(){return state?copy(state):null}
export function persistServerProfiles(next:StoredApiProfiles){
 state=copy(next);generation++;clearTimeout(timer);timer=setTimeout(()=>{void flushServerProfiles().catch(error=>window.dispatchEvent(new CustomEvent('fingerpoint-profile-sync-error',{detail:error.message})))},600)
}
export async function flushServerProfiles():Promise<void>{
 clearTimeout(timer)
 if(blocked)throw Error('其他电脑已修改配置。请导出当前配置后刷新，避免覆盖。')
 if(flight){await flight;if(savedGeneration<generation)return flushServerProfiles();return}
 if(!state||savedGeneration===generation)return
 const target=generation,payload=copy(state)
 flight=(async()=>{try{const result=await request('PUT',{revision,state:payload});revision=result.revision;savedGeneration=target}catch(error){if(error instanceof Error&&error.message.startsWith('其他电脑'))blocked=true;throw error}})()
 try{await flight}finally{flight=null}
 if(savedGeneration<generation)return flushServerProfiles()
}
export function subscribeServerProfiles(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener)}}
async function refresh(){
 if(flight||generation!==savedGeneration||blocked||document.hidden)return
 try{const result=await request();if(flight||generation!==savedGeneration||blocked)return;if(result.revision!==revision){state=result.state;revision=result.revision;listeners.forEach(f=>f())}}catch{/* keep edits in memory; explicit save reports failures */}
}
window.addEventListener('focus',()=>{void refresh()})
setInterval(()=>{void refresh()},10000)
window.addEventListener('beforeunload',event=>{if(generation!==savedGeneration){event.preventDefault();event.returnValue=''}})
