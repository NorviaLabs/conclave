// The canvas context menu. Actions are what belong to the thing that was
// right-clicked, and every item carries the same data attributes as the control
// it stands for, so the menu is a shortcut rather than a second code path.
//
// Markup only: studio.js owns opening, closing, positioning and keyboard
// navigation, and the existing delegated click handler runs the items.

// An item is one menu entry. `attrs` are the page's own hooks (`data-plan-*`,
// `data-action`, ...), so an item never introduces a parallel vocabulary.
export function menuItem(label,attrs={},options={}){
  return {label,attrs,...options};
}
export function menuSeparator(){
  return {separator:true};
}

export function menuItemMarkup(item,escape){
  if(item.separator)return '<span class="canvas-menu-separator" role="separator"></span>';
  const attributes=Object.entries(item.attrs||{}).map(([name,value])=>` ${name}="${escape(String(value))}"`).join('');
  const title=item.disabled&&item.why?` title="${escape(item.why)}"`:'';
  return `<button type="button" role="menuitem" tabindex="-1" class="canvas-menu-item${item.destructive?' destructive':''}"${item.disabled?' disabled aria-disabled="true"':''}${title}${attributes}>${escape(item.label)}</button>`;
}

// `label` names the menu for a screen reader, e.g. "Step actions: Draft the brief".
export function menuMarkup({label,items,point,escape}){
  return `<div class="canvas-menu" role="menu" aria-label="${escape(label)}" data-canvas-menu style="left:${Math.round(point.x)}px;top:${Math.round(point.y)}px">${items.map(item=>menuItemMarkup(item,escape)).join('')}</div>`;
}

// Where a press landed. Nodes win over edges win over the background, and only
// surfaces this application owns answer at all: the native menu stays available
// everywhere else, including inside the settings fields.
export function menuTarget(target){
  if(!target?.closest)return null;
  // A field keeps the browser's own menu: text selection and paste still work.
  if(target.closest('input,textarea,select,[contenteditable="true"]'))return null;
  const node=target.closest('[data-plan-node],[data-run-node]');
  if(node){
    const run=node.hasAttribute('data-run-node');
    return {kind:run?'run-node':'plan-node',id:node.getAttribute(run?'data-run-node':'data-plan-node')};
  }
  const edge=target.closest('[data-canvas-edge]');
  if(edge){
    const [from,to]=String(edge.getAttribute('data-canvas-edge')).split('|');
    return from&&to?{kind:'plan-edge',id:edge.getAttribute('data-canvas-edge'),from,to}:null;
  }
  const canvas=target.closest('[data-canvas-mode]');
  if(!canvas)return null;
  const inViewport=target.closest('.canvas-viewport');
  if(!inViewport)return null;
  return {kind:canvas.getAttribute('data-canvas-mode')==='run'?'run-canvas':'plan-canvas'};
}

// Roving focus over the items a person can actually reach.
export function menuFocusTargets(menu){
  return [...menu.querySelectorAll('[role="menuitem"]')].filter(item=>!item.disabled);
}
