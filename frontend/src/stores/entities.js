import {nodesAPI,scenariosAPI,drillsAPI,rollupAPI} from '../api/entities.js';
export const store={nodes:[],scenarios:[],drills:[],rollup:null,async refresh(){[this.nodes,this.scenarios,this.drills,this.rollup]=await Promise.all([nodesAPI.list(),scenariosAPI.list(),drillsAPI.list(),rollupAPI.summary()]);}};
