// Injected by the local dev server only; never bundled into production assets.
export const reloadClient=`<script>
let version;
setInterval(async()=>{
  try{
    const response=await fetch('/__studio_reload',{cache:'no-store'});
    if(!response.ok)return;
    const next=await response.json();
    if(version!==undefined&&next.revision!==version.revision){
      if(next.reload!==version.reload)location.reload();
      else document.querySelectorAll('link[rel="stylesheet"]').forEach(link=>{
        const url=new URL(link.href);url.searchParams.set('dev',next.revision);link.href=url.href;
      });
    }
    version=next;
  }catch{}
},750);
</script>`;

export function injectReload(html){return html.replace('</body>',reloadClient+'</body>');}
