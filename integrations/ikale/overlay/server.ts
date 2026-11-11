import { publicOrigin, internalOrigin } from './linx-env'
import { profileRequest } from './profile-store'
import { proxyRequest } from './worker/main.js'
import { resolve, sep } from 'node:path'
const root = resolve(import.meta.dir, 'web/dist')
const prefix = '/detector/'
Bun.serve({
 hostname: '127.0.0.1', port: 8789, idleTimeout: 255,
 async fetch(request) {
  const url = new URL(request.url)
  if (!url.pathname.startsWith(prefix)) return new Response('Not found', {status:404})
  const profiles = url.pathname === prefix + 'api/profiles'
  const api = url.pathname.startsWith(prefix + 'api/')
  try {
   const auth = await fetch(internalOrigin+'/api/detector/auth', {headers:{Cookie:request.headers.get('cookie') || ''},redirect:'manual',signal:AbortSignal.timeout(5000)})
   await auth.body?.cancel()
   if (auth.status !== 204) return api ? Response.json({error:{message:'请先登录编辑器'}},{status:401,headers:{'Cache-Control':'no-store'}}) : Response.redirect(publicOrigin+'/login',302)
  } catch {return new Response('登录服务暂不可用',{status:503})}
  if (profiles) { try { return await profileRequest(request) } catch { return Response.json({error:'服务器配置存储暂不可用'},{status:503,headers:{'Cache-Control':'no-store'}}) } }
  if (api) {
   const relay = new URL(request.url); relay.pathname='/api/proxy'
   return proxyRequest(new Request(relay,request), {allowedOrigins:[publicOrigin]})
  }
  if (request.method !== 'GET' && request.method !== 'HEAD') return new Response('Method not allowed',{status:405})
  let path
  try {path=decodeURIComponent(url.pathname.slice(prefix.length))} catch {return new Response('Bad path',{status:400})}
  if (path.split('/').some(part=>part.startsWith('.') || part.startsWith('_'))) return new Response('Not found',{status:404})
  const full=resolve(root,path || 'index.html')
  if (full!==root && !full.startsWith(root+sep)) return new Response('Not found',{status:404})
  let file=Bun.file(full)
  if (!await file.exists()) {
   if (path.includes('.')) return new Response('Not found',{status:404})
   file=Bun.file(resolve(root,'index.html'))
  }
  return new Response(request.method==='HEAD'?null:file,{headers:{'Content-Type':file.type,'X-Content-Type-Options':'nosniff','Cache-Control':path.startsWith('assets/')?'private, max-age=31536000, immutable':'no-cache'}})
 }
})
console.log('LM Detector listening on 127.0.0.1:8789/detector/')
