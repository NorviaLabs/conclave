// Keep live surfaces mounted: scroll, focus, pointer capture and disclosures
// belong to the user, not to each incoming execution record.
export function updateRunDOM(root,markup){
  const template=document.createElement('template');template.innerHTML=markup;
  const key=node=>node.nodeType===1?(node.id||['runNode','runStep','planNode','planSelect','canvasPort','canvasEdge','path','disclosure','activityId','action','humanField','humanInput'].map(name=>node.dataset[name]?`${name}:${node.dataset[name]}`:'').find(Boolean)||''):'';
  function sync(parent,next){
    const keyed=new Map([...parent.childNodes].filter(key).map(node=>[key(node),node]));
    let cursor=parent.firstChild;
    for(const fresh of [...next.childNodes]){
      let node=key(fresh)?keyed.get(key(fresh)):cursor;
      if(!node||node.nodeType!==fresh.nodeType||node.nodeName!==fresh.nodeName||(!key(fresh)&&key(node))){node=fresh.cloneNode(true);parent.insertBefore(node,cursor);}
      else{
        if(node!==cursor)parent.insertBefore(node,cursor);
        if(node.nodeType===3){if(node.data!==fresh.data)node.data=fresh.data;}
        else if(node.nodeType===1){
          for(const attribute of [...node.attributes])if(!fresh.hasAttribute(attribute.name)&&!(node.tagName==='DETAILS'&&attribute.name==='open'))node.removeAttribute(attribute.name);
          for(const attribute of [...fresh.attributes])if(!(node.tagName==='DETAILS'&&attribute.name==='open')&&node.getAttribute(attribute.name)!==attribute.value)node.setAttribute(attribute.name,attribute.value);
          if(node!==document.activeElement||!['INPUT','TEXTAREA','SELECT'].includes(node.tagName))sync(node,fresh);
          if(node!==document.activeElement&&['INPUT','TEXTAREA','SELECT'].includes(node.tagName)){node.value=fresh.value;if(node.tagName==='INPUT')node.checked=fresh.checked;}
        }
      }
      cursor=node.nextSibling;
    }
    while(cursor){const next=cursor.nextSibling;cursor.remove();cursor=next;}
  }
  sync(root,template.content);
}
