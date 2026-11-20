import {api} from './client.js';
export const nodesAPI={list:()=>api('/nodes'),save:(data,id)=>api('/nodes'+(id?'/'+id:''),{method:id?'PUT':'POST',body:JSON.stringify(data)})};
export const scenariosAPI={list:()=>api('/scenarios'),create:data=>api('/scenarios',{method:'POST',body:JSON.stringify(data)})};
export const drillsAPI={list:()=>api('/drills'),get:id=>api('/drills/'+id),create:data=>api('/drills',{method:'POST',body:JSON.stringify(data)}),
  corrections:(id,data)=>api('/drills/'+id+'/corrections',{method:'POST',body:JSON.stringify(data)}),
  listCorrections:id=>api('/drills/'+id+'/corrections')};
export const rollupAPI={summary:()=>api('/rollup/summary'),backfill:()=>api('/rollup/backfill',{method:'POST'})};
