// One canvas surface and card anatomy; modes supply data and permitted actions.
export function uiIcon(name){
  const paths={
    minus:'M5 12h14',plus:'M5 12h14M12 5v14',
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
    person:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20a7.5 7.5 0 0 1 15 0'
  };
  return `<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="${paths[name]}"></path></svg>`;
}
export function canvasFitZoom(viewWidth,viewHeight,boardWidth,boardHeight){
  return Math.min(1,Math.max(1,viewWidth-64)/Math.max(1,boardWidth),Math.max(1,viewHeight-112)/Math.max(1,boardHeight));
}
export function canvasStepMarkup({id,index,human,selected,position,escape:e,mode,header='',content='',footer='',stale=false,status='waiting'}){
  const run=mode==='run';
  return `<section class="plan-node compact ${run?'run-node':''} ${human?'human':''} ${selected?'selected':''} ${stale?'stale':''}" ${run?'data-run-node':'data-plan-node'}="${e(id)}"${run?` data-status="${e(status)}"`:''}${stale?' data-run-stale="true"':''}${run?'':` data-plan-tool-target="${e(id)}"`} style="left:${position.x}px;top:${position.y}px"><div class="plan-node-head"><span class="plan-number">${index+1}</span><span class="kind-tag ${human?'human':'ai'}">${human?'Human':'AI'}</span>${header}</div>${content}${footer}</section>`;
}
export function canvasMarkup({mode,width,height,metrics,edges,nodes,overlay='',dock='',zoom=1}){
  const run=mode==='run',zoomAttribute=run?'data-run-zoom':'data-canvas-zoom';
  return `<div class="plan-canvas canvas-viewport graph-canvas" data-canvas-mode="${mode}" tabindex="0" aria-label="${run?'Run canvas':'Work plan'}" aria-description="Drag the background or use arrow keys to pan. Use Fit to show the whole graph."><div class="canvas-flow" ${run?'data-run-canvas':'data-canvas-drop="plan"'} style="width:${width}px;height:${height}px;--node-width:${metrics.nodeWidth}px;--node-height:${metrics.nodeHeight}px"><svg class="canvas-edges" ${run?'data-run-canvas-edges':'data-canvas-edges'} width="${width}" height="${height}" aria-hidden="true">${edges}</svg>${overlay}${nodes}</div>${dock}<div class="canvas-view" role="group" aria-label="Canvas view"><button type="button" ${zoomAttribute}="out" aria-label="Zoom out" title="Zoom out">${uiIcon('minus')}</button><button type="button" class="canvas-view-level" ${zoomAttribute}="reset" aria-label="Reset to 100%" title="Reset to 100%">${Math.round((zoom||1)*100)}%</button><button type="button" ${zoomAttribute}="in" aria-label="Zoom in" title="Zoom in">${uiIcon('plus')}</button><button type="button" ${zoomAttribute}="fit" aria-label="Fit entire graph" title="Fit entire graph">${uiIcon('fit')}</button>${run?'':`<button type="button" data-action="canvas-fullscreen" aria-pressed="false" aria-label="Full screen" title="Full screen">${uiIcon('expand')}</button>`}</div></div>`;
}

// The canvas action bar: one floating bar per canvas. The run controller and the
// plan tools share it, and a control that cannot act right now is disabled
// rather than removed, so the bar never changes shape under a person's hands.
export function planToolsMarkup(disabled=false,expanded=false){
  // Icons only: the bar is a strip of symbols, so each one carries its name in
  // the hovertext and for a screen reader. A disabled control keeps its own name
  // and says why in the hovertext, so the reason never replaces what it does.
  const why='finish or stop the run to change the plan';
  const name=(label)=>disabled?`${label} · ${why}`:label;
  const off=disabled?'disabled':'';
  return `<button type="button" class="canvas-action" data-action="toggle-plan-resources" aria-expanded="${expanded}" ${off} title="${name('Add step')}" aria-label="Add step">${uiIcon('plus')}</button><button type="button" class="canvas-action" data-action="suggest-plan-change" ${off} title="${name('Suggest a change to the plan')}" aria-label="Suggest a change to the plan">${uiIcon('sparkle')}</button><button type="button" class="canvas-action" data-action="tidy-canvas" ${off} title="${name('Arrange positions only; keep connections')}" aria-label="Arrange positions only; keep connections">${uiIcon('tidy')}</button>`;
}
export function canvasDockMarkup({controller='',toolsDisabled=false,resourcesOpen=false}={}){
  return `<div class="canvas-dock" role="group" aria-label="Canvas controls" data-canvas-dock><span class="canvas-dock-grip" aria-hidden="true">${uiIcon('grip')}</span>${controller?`<span class="canvas-dock-group" role="group" aria-label="Run controls">${controller}</span><span class="canvas-dock-separator" aria-hidden="true"></span>`:''}${planToolsMarkup(toolsDisabled,resourcesOpen)}</div>`;
}
