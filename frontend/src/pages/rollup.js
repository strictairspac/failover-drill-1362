import {rollupAPI} from '../api/entities.js';
import {session} from '../stores/session.js';
import {table} from '../components/common/table.js';
import {esc} from '../utils/html.js';
import {message} from '../hooks/action.js';
import {VERDICTS} from '../constants/enums.js';

export function rollupPage(s){
 if(!s)return '<section class="empty"><h2>复盘汇总载入中</h2><p>请检查服务状态后重试。</p></section>';
 const c=s.counts,p=s.progress;
 const modeText=s.cutover?'已切换 · 汇总增量模式（旧演练不再重扫）':'补算中 · 实时全量口径（查询已可查准）';
 const pct=p.total_drills?Math.round(p.covered_drills/p.total_drills*100):100;
 return `<div class="section-title"><h2>值班复盘场数</h2><span class="badge ${s.cutover?'completed':'failed'}">${esc(modeText)}</span></div>
 <section class="count-cards">
  <div class="count-card pass"><p>通过</p><h2>${c.approved}</h2></div>
  <div class="count-card block"><p>阻断</p><h2>${c.blocked}</h2></div>
  <div class="count-card fail"><p>失败</p><h2>${c.failed}</h2></div>
  <div class="count-card total"><p>合计场次</p><h2>${c.total}</h2></div>
 </section>
 <div class="section-title"><h2>历史补算进度</h2><span>每批 ${p.batch_size} 场 · 中断可从游标恢复</span></div>
 <div class="progress"><div class="progress-bar" style="width:${pct}%"></div></div>
 ${table(['指标','数值'],[
   ['账本已覆盖演练',`${p.covered_drills} / ${p.total_drills} 场（${pct}%）`],
   ['补算游标（最大演练编号）',String(p.last_drill_id)],
   ['游标后待并入新演练',`${p.remaining_after_cursor} 场（查询时实时合并，不重扫旧演练）`],
   ['汇总计数覆盖场数',String(s.rollup.counted_drills)],
   ['事件账本游标',String(s.rollup.last_event_id)],
   ['最近更新时间',s.updated_at?esc(s.updated_at.replace('T',' ').slice(0,19)):'—']])}
 <div class="toolbar">
  ${session.role==='admin'?`<button id="backfill">推进下一批补算 →</button>`:''}
  <button class="secondary" id="refresh-rollup">刷新场数与进度</button>
 </div>
 <p class="readonly">口径：${esc(Object.entries(VERDICTS).map(([k,v])=>`${v}=${k}`).join('；'))}。每场演练只按当前裁决计一次；人工更正产生新版本事件，汇总增量调整，不重复计数。</p>`;
}

export function bindRollup(render){
 document.querySelector('#refresh-rollup')?.addEventListener('click',render);
 const btn=document.querySelector('#backfill');
 if(btn)btn.addEventListener('click',async()=>{
   btn.disabled=true;
   try{
    const r=await rollupAPI.backfill();
    message(r.switched?'历史已补齐，已一次性切换到汇总可查状态':`本批扫描 ${r.scanned} 场，新增入账 ${r.inserted} 场`);
    await render();
   }catch(e){message(e.message,true);btn.disabled=false;}
 });
}
