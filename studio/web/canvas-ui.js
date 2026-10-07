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
    play:'M7 4l14 8-14 8z',tidy:'M3 4h6v6H3zM15 14h6v6h-6zM9 7h9v7'
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
