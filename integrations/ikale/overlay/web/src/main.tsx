import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initializeProfiles } from "./lib/server-profiles";
import { toast } from "sonner";
import "./index.css";
window.addEventListener('fingerpoint-profile-sync-error',event=>toast.error((event as CustomEvent<string>).detail));
async function start(){
 try {
  await initializeProfiles();
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

 } catch(error) {
  const root=document.getElementById('root')!;root.replaceChildren();
  const box=document.createElement('div');box.className='fp-page';
  const message=document.createElement('p');message.textContent=error instanceof Error?error.message:'无法读取服务器配置，请重试';
  const retry=document.createElement('button');retry.textContent='重新连接';retry.onclick=()=>{retry.disabled=true;void start()};box.append(message,retry);root.append(box);
 }
}
void start();
