'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const start=source.indexOf('function npu(){'),end=source.indexOf('\nfunction kv(',start);
assert(start>=0&&end>start,'NPU view function is available');
function element(){return {style:{},children:[],append(...children){this.children.push(...children);},replaceChildren(...children){this.children=[...children];}};}
function render(classes,show=true){
  const values={},probabilities=element(),scores=classes.map((_,i)=>i===classes.length-1?1:0);
  const context={state:{mode:'live',server_time:10,aoa:{run_id:'pm-test',beam:{acquisition:{applied_mode:show?'identify':'locate'}}},npu:show?{run_id:'pm-test',received_at:9,status:'inferred',npu_executed:true,classification:{classes,probabilities:scores}}:null},
    document:{createElement:element},$:id=>{assert.equal(id,'probabilities');return probabilities;},
    text:(id,value)=>{values[id]=value;},fmt:(value,digits)=>value.toFixed(digits),handoffs:{}};
  vm.createContext(context);vm.runInContext(source.slice(start,end)+'\nnpu();',context);
  return {values,rows:probabilities.children};
}
const six=render(['FM','AM','CW','noise','OOK','PM']);
assert.equal(six.values.class,'PM');assert.equal(six.values['class-name'],'相位调制');
assert.equal(six.rows.length,6);assert.equal(six.rows[5].children[0],'PM');assert.equal(six.rows[5].children[2],'100.0%');
assert.equal(six.rows[5].className,'ao-prob active');
const five=render(['FM','AM','CW','noise','OOK']);
assert.equal(five.values.class,'OOK');assert.equal(five.rows.length,5);assert.equal(five.rows[4].children[0],'OOK');
const paused=render([],false);
assert.equal(paused.values['npu-state'],'NPU 已暂停');assert.equal(paused.values.class,'—');
assert.deepEqual(paused.rows.map(row=>row.children[0]),['FM','AM','CW','noise','OOK','PM']);
assert.ok(paused.rows.every(row=>row.children[2]==='—'&&row.children[1].children[0].style.width==='0%'));
console.log('PASS paused six-class placeholders, PM six-row/name/probability and legacy five-row view');
