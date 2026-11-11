import { publicOrigin } from './linx-env'
import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync, renameSync, existsSync, chmodSync } from 'node:fs'
import { resolve } from 'node:path'
const dir=resolve(import.meta.dir,'private'),file=resolve(dir,'profiles.enc'),keyFile=resolve(dir,'profiles.key')
mkdirSync(dir,{recursive:true,mode:0o700});chmodSync(dir,0o700)
if(!existsSync(keyFile))writeFileSync(keyFile,randomBytes(32),{flag:'wx',mode:0o600})
const key=readFileSync(keyFile)
type Stored={revision:number,state:unknown}
function read():Stored {
 if(!existsSync(file))return {revision:0,state:null}
 const raw=readFileSync(file),iv=raw.subarray(0,12),tag=raw.subarray(12,28),cipher=createDecipheriv('aes-256-gcm',key,iv);cipher.setAuthTag(tag)
 return JSON.parse(Buffer.concat([cipher.update(raw.subarray(28)),cipher.final()]).toString())
}
function valid(state:any){
 if(!state||!Array.isArray(state.profiles)||state.profiles.length<1||state.profiles.length>200||typeof state.activeId!=='string'||!state.savedProfiles||typeof state.savedProfiles!=='object')return false
 const ids=new Set();for(const p of state.profiles){if(!p||typeof p.id!=='string'||!p.id||ids.has(p.id))return false;ids.add(p.id);for(const field of ['name','baseUrl','apiKey','model','effort'])if(typeof p[field]!=='string'||p[field].length>16384)return false;if(!['openai','responses','anthropic'].includes(p.format))return false;for(const f of ['stream','parallel','relaxed','autoVerify','tokenizerProbe'])if(typeof p[f]!=='boolean')return false}return ids.has(state.activeId)
}
const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
export async function profileRequest(request:Request){
 if(request.method==='GET')return Response.json(read(),{headers})
 if(request.method!=='PUT')return new Response(null,{status:405,headers})
 if(request.headers.get('origin')!==publicOrigin||!request.headers.get('content-type')?.startsWith('application/json'))return Response.json({error:'请求来源不正确'},{status:403,headers})
 if(Number(request.headers.get('content-length')||0)>2_000_000)return new Response(null,{status:413,headers})
 const text=await request.text();if(text.length>2_000_000)return new Response(null,{status:413,headers})
 let body;try{body=JSON.parse(text)}catch{return new Response(null,{status:400,headers})}
 if(!valid(body.state)||!Number.isSafeInteger(body.revision))return Response.json({error:'配置格式不正确'},{status:400,headers})
 const previous=read();if(previous.revision!==body.revision)return Response.json({error:'其他电脑已更新配置，请刷新后重试',revision:previous.revision},{status:409,headers})
 const result={revision:previous.revision+1,state:body.state},iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv),encrypted=Buffer.concat([cipher.update(JSON.stringify(result)),cipher.final()]);const temporary=file+'.tmp';writeFileSync(temporary,Buffer.concat([iv,cipher.getAuthTag(),encrypted]),{mode:0o600});renameSync(temporary,file);return Response.json({revision:result.revision},{headers})
}
