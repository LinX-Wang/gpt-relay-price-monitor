import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
const envFile=resolve(import.meta.dir,'../../.env')
if(existsSync(envFile))for(const raw of readFileSync(envFile,'utf8').split(/\r?\n/)){
 const line=raw.trim();if(!line||line.startsWith('#'))continue
 const split=line.indexOf('=');if(split<1)continue
 const key=line.slice(0,split).trim();let value=line.slice(split+1).trim()
 if(value.length>1&&((value.startsWith('"')&&value.endsWith('"'))||(value.startsWith("'")&&value.endsWith("'"))))value=value.slice(1,-1)
 if(/^(APP_|IKALE_)/.test(key)&&process.env[key]===undefined)process.env[key]=value
}
export const publicOrigin=(process.env.APP_PUBLIC_URL||'http://127.0.0.1:8765').replace(/\/$/,'')
export const internalOrigin=(process.env.APP_INTERNAL_URL||'http://127.0.0.1:'+(process.env.APP_PORT||'8765')).replace(/\/$/,'')
