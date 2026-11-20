import {store} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {drillsAPI,rollupAPI} from '../api/entities.js';
import {table} from '../components/common/table.js';
import {badge} from '../components/common/status.js';
import {esc,formObject} from '../utils/html.js';
import {action} from '../hooks/action.js';
import {VERDICTS} from '../constants/enums.js';
const verdictCell=v=>`<span class="verdict-tag ${esc(v)}">${esc(VERDICTS[v]||v)}</span>`;
export function drillsPage(rollup){
 const cards=rollup?`<section class="count-cards compact-cards">
  <div class="count-card pass"><p>通过</p><h2>${rollup.counts.approved}</h2></div>
  <div class="count-card block"><p>阻断</p><h2>${rollup.counts.blocked}</h2></div>
  <div class="count-card fail"><p>失败</p><h2>${rollup.counts.failed}</h2></div>
  <div class="count-card total"><p>合计</p><h2>${rollup.counts.total}</h2></div>
 </section><p class="checksum">${rollup.cutover?'汇总增量模式：旧演练不重扫，新演练实时合并':'补算进行中：当前为实时全量口径，场数已查准'} · 覆盖 ${rollup.progress.covered_drills}/${rollup.progress.total_drills} 场 · <a href="#rollup">复盘汇总与补算 →</a></p>`:'';
 return `<section class="split compact"><div><h2>提交一次演练</h2><p>节点与剧本在提交瞬间冻结。后台计算后，查看判定依据和导出结果。</p></div>${session.role!=='viewer'?`<form id="drill-form"><label>故障剧本<select name="scenario_id">${store.scenarios.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></label><label>演练标签<input name="label" value="晚班切主核验" required maxlength="120"></label><button type="submit">冻结快照并入队 →</button></form>`:'<p class="readonly">观察员可复核下方记录。</p>'}</section>
 <div class="section-title"><h2>复盘场数</h2><button class="secondary" id="refresh">刷新状态</button></div>${cards}
 <div class="section-title"><h2>最近演练记录</h2><span>当前裁决含人工更正</span></div>
 ${table(['编号','演练','执行状态','当前裁决','证据'],store.drills.map(d=>[d.id,esc(d.label),badge(d.status),verdictCell(d.current_verdict),`<a href="#detail/${d.id}">查看记录 →</a>`]))}`;
}
export function bindDrills(render){document.querySelector('#refresh').onclick=render;action(document.querySelector('#drill-form'),async f=>{const d=formObject(f);d.scenario_id=Number(d.scenario_id);const job=await drillsAPI.create(d);location.hash='#detail/'+job.id;});}
