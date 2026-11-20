/** @typedef {{id:number,name:string,role:'leader'|'follower',term:number,log_position:number,available:boolean,fenced:boolean,vote_enabled:boolean}} Node */
/** @typedef {{id:number,name:string,fail_primary:boolean,fence_primary:boolean,max_data_loss:number,required_term:number}} Scenario */
/** @typedef {{id:number,scenario_id:number,label:string,status:string,snapshot:object,result:object|null,override_verdict?:string,current_verdict:'approved'|'blocked'|'failed',overrides:Array<{version:number,verdict:string,reason:string,actor:string,created_at:string}>}} Drill */
/** @typedef {{mode:'live'|'rollup',cutover:boolean,counts:{approved:number,blocked:number,failed:number,total:number},rollup:object,progress:{covered_drills:number,total_drills:number,remaining_after_cursor:number,batch_size:number,last_drill_id:number},updated_at:string|null}} RollupSummary */
export {};
