// One canvas surface and card anatomy; modes supply data and permitted actions.
export function uiIcon(name){
  const paths={
    minus:'M5 12h14',check:'M5 12.5l4.5 4.5L19 7.5',warning:'M12 4 2.5 20h19zM12 10v4.5M12 17.2h.01',plus:'M5 12h14M12 5v14',
    fit:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M8 8h8v8H8z',
    expand:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5',
    collapse:'M3 8h5V3m8 0v5h5M8 21v-5H3m18 0h-5v5',
    close:'M6 6l12 12M6 18L18 6',copy:'M9 9h12v12H9zM15 9V3H3v12h6',
    trash:'M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7',
    up:'M12 20V4m-6 6l6-6 6 6',down:'M12 4v16m-6-6l6 6 6-6',
    play:'M7 4l14 8-14 8z',pause:'M9 5v14M15 5v14',stop:'M6 6h12v12H6z',tidy:'M3 4h6v6H3zM15 14h6v6h-6zM9 7h9v7',
    clock:'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
    info:'M12 11v6M12 8h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z',
    download:'M12 3v12m-5-5 5 5 5-5M4 20h16',
    pencil:'M4 20h4L20 8l-4-4L4 16zM14 6l4 4',
    sliders:'M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M8 14v6',
    chevron:'M6 9l6 6 6-6',
    list:'M4 6h16M4 12h16M4 18h10',
    sparkle:'M12 4l1.8 5.2L19 11l-5.2 1.8L12 18l-1.8-5.2L5 11l5.2-1.8zM18.5 16.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z',
    grip:'M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01',
    person:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20a7.5 7.5 0 0 1 15 0',
    link:'M9.5 14.5l5-5M7 12l-1.5 1.5a3.5 3.5 0 0 0 5 5L12 17M12 7l1.5-1.5a3.5 3.5 0 0 1 5 5L17 12',
    centre:'M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z'
  };
  return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="${paths[name]}"></path></svg>`;
}
// The dock and the zoom control float over the bottom of the canvas; a fitted
// plan sits above them.
export const CANVAS_DOCK_SPACE=84;
export function canvasFitZoom(viewWidth,viewHeight,boardWidth,boardHeight){
  return Math.min(1,Math.max(1,viewWidth-64)/Math.max(1,boardWidth),Math.max(1,viewHeight-CANVAS_DOCK_SPACE-40)/Math.max(1,boardHeight));
}
export function canvasStepMarkup({id,index,human,selected,position,escape:e,mode,header='',content='',footer='',stale=false,status='waiting',tested=false,branch=false,connectTarget=false}){
  const run=mode==='run';
  return `<section class="plan-node compact ${run?'run-node':''} ${human?'human':''} ${selected?'selected':''} ${branch?'branch':''} ${stale?'stale':''} ${tested?'tested':''} ${connectTarget?'connect-target':''}" ${run?'data-run-node':'data-plan-node'}="${e(id)}"${run?` data-status="${e(status)}"`:''}${stale?' data-run-stale="true"':''}${tested?' data-run-tested="true"':''}${branch?' data-plan-branch="true"':''}${run?'':` data-plan-tool-target="${e(id)}"`} style="left:${position.x}px;top:${position.y}px"><div class="plan-node-head"><span class="plan-number">${index+1}</span><span class="kind-tag ${human?'human':'ai'}">${human?'Human':'AI'}</span>${header}</div>${content}${footer}</section>`;
}
export function canvasContentScrolls(target,area,deltaX,deltaY){
  for(let node=target;node&&node!==area;node=node.parentElement){
    const style=getComputedStyle(node);
    if(/auto|scroll/.test(style.overflowY)&&node.scrollHeight>node.clientHeight&&deltaY)return true;
    if(/auto|scroll/.test(style.overflowX)&&node.scrollWidth>node.clientWidth&&deltaX)return true;
  }
  return false;
}
export function canvasMarkup({mode,width,height,metrics,edges,nodes,overlay='',dock='',zoom=1}){
  const run=mode==='run',zoomAttribute=run?'data-run-zoom':'data-canvas-zoom';
  return `<div class="plan-canvas canvas-viewport graph-canvas" data-canvas-mode="${mode}" tabindex="0" aria-label="${run?'Run canvas':'Work plan'}" aria-description="Drag the background, scroll or use the arrow keys to pan; hold Command or Control and scroll to zoom. The canvas has no edge: pan as far as you like. Use Fit to show the whole graph."><div class="canvas-flow" ${run?'data-run-canvas':'data-canvas-drop="plan"'} style="width:${width}px;height:${height}px;--node-width:${metrics.nodeWidth}px;--node-height:${metrics.nodeHeight}px"><svg class="canvas-edges" ${run?'data-run-canvas-edges':'data-canvas-edges'} width="${width}" height="${height}" ${run?'aria-hidden="true"':'role="group" aria-label="Workflow connections"'}>${edges}</svg>${overlay}${nodes}</div>${dock}<div class="canvas-view" role="group" aria-label="Canvas view"><button type="button" ${zoomAttribute}="out" aria-label="Zoom out" title="Zoom out">${uiIcon('minus')}</button><button type="button" class="canvas-view-level" ${zoomAttribute}="reset" aria-label="Reset to 100%" title="Reset to 100%">${Math.round((zoom||1)*100)}%</button><button type="button" ${zoomAttribute}="in" aria-label="Zoom in" title="Zoom in">${uiIcon('plus')}</button></div></div>`;
}

// The canvas action bar: one floating bar per canvas. The run controller and the
// plan tools share it, and a control that cannot act right now is disabled
// rather than removed, so the bar never changes shape under a person's hands.
// What the dock shows for a selected step. These are the context menu's own
// commands — same attributes, same handlers — offered where a person is already
// looking, because a selection is what the canvas is about at that moment.
// Removing a step stays behind the right-click: a stray click must not delete.
function selectionMarkup(id,escape,view){
  const action=(label,attribute,icon)=>`<button type="button" class="canvas-action" ${attribute} title="${escape(label)}" aria-label="${escape(label)}">${uiIcon(icon)}</button>`;
  // A run's steps are not the plan's steps: what you can do to a step you are
  // watching is read it, find it in the activity, and take its output. Editing
  // the plan waits until the run is over, as it does everywhere else.
  const actions=view==='run'
    ? [action('Step details',`data-run-step="${escape(id)}"`,'info'),
       action('Show this step in activity',`data-menu-step-activity="${escape(id)}"`,'list'),
       action('Copy this step’s output',`data-menu-copy-output="${escape(id)}"`,'copy')]
    : [action('Edit step',`data-plan-select="${escape(id)}"`,'pencil'),
       action('Duplicate step',`data-plan-duplicate="${escape(id)}"`,'copy'),
       action('Connect to another step',`data-canvas-port="${escape(id)}"`,'link'),
       action('Centre in view',`data-menu-centre="${escape(id)}"`,'centre')];
  return `<span class="canvas-dock-group" role="group" aria-label="Selected step">${actions.join('')}</span><span class="canvas-dock-separator" aria-hidden="true"></span>`;
}
export function planToolsMarkup(disabled=false,expanded=false,{selection='',view='plan',escape=value=>String(value??'')}={}){
  // Primary actions keep labels; navigation symbols carry their names in
  // the hovertext and for a screen reader. A disabled control keeps its own name
  // and says why in the hovertext, so the reason never replaces what it does.
  const why='finish or stop the run to change the plan';
  const name=(label)=>disabled?`${label} · ${why}`:label;
  const off=disabled?'disabled':'';
  // Fit belongs beside the actions now that the canvas has no edge: it is the way
  // back from anywhere. Full screen stays on the plan, where it means something.
  const zoomAttribute=view==='run'?'data-run-zoom':'data-canvas-zoom';
  const viewGroup=`<span class="canvas-dock-separator" aria-hidden="true"></span><span class="canvas-dock-group" role="group" aria-label="Canvas view">`
    + `<button type="button" class="canvas-action" ${zoomAttribute}="fit" title="Fit entire graph" aria-label="Fit entire graph">${uiIcon('fit')}</button>`
    + (view==='run'?'':`<button type="button" class="canvas-action" data-action="canvas-fullscreen" aria-pressed="false" title="Full screen" aria-label="Full screen">${uiIcon('expand')}</button>`)
    + `</span>`;
  return `${selection?selectionMarkup(selection,escape,view):''}${view==='plan'?`<button type="button" class="canvas-action labelled" data-step-test="author" data-test-step="${escape(selection)}" ${!selection||disabled?'disabled':''} title="${selection?'Test the selected step safely':'Select a step to test'}">${uiIcon('play')}<span>Test step</span></button>`:''}<button type="button" class="canvas-action" data-action="toggle-plan-resources" aria-expanded="${expanded}" ${off} title="${name('Add step')}" aria-label="Add step">${uiIcon('plus')}<span>Add step</span></button><button type="button" class="canvas-action" data-action="suggest-plan-change" ${off} title="${name('Suggest a change to the plan')}" aria-label="Suggest a change to the plan">${uiIcon('sparkle')}</button><button type="button" class="canvas-action" data-action="tidy-canvas" ${off} title="${name('Arrange positions only; keep connections')}" aria-label="Arrange positions only; keep connections">${uiIcon('tidy')}</button>${viewGroup}`;
}
export function canvasDockMarkup({controller='',toolsDisabled=false,resourcesOpen=false,selection='',view='plan',escape}={}){
  return `<div class="canvas-dock" role="group" aria-label="Canvas controls" data-canvas-dock>${controller?`<span class="canvas-dock-group" role="group" aria-label="Run controls">${controller}</span><span class="canvas-dock-separator" aria-hidden="true"></span>`:''}${planToolsMarkup(toolsDisabled,resourcesOpen,{selection,view,escape})}</div>`;
}
