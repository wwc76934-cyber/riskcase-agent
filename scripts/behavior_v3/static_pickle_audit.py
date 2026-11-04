# Symbolic pickle opcode walker: NEVER imports or calls GLOBAL/REDUCE/NEWOBJ targets.
import pickletools,json
from pathlib import Path
import argparse
p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);args=p.parse_args()
infile=Path(args.input);r=Path(args.output);r.mkdir(parents=True,exist_ok=False)
assert infile.stat().st_size<=150_000_000
stack=[];memo={};mark=object()
def node(kind,*args):return {'kind':kind,'args':args}
def popmark():
 i=len(stack)-1
 while stack[i] is not mark:i-=1
 out=stack[i+1:];del stack[i:];return out
with infile.open('rb') as f:
 for count,(op,arg,pos) in enumerate(pickletools.genops(f)):
  if count>500000:raise ValueError('Opcode budget')
  n=op.name
  if n in ('PROTO','FRAME'):continue
  if n=='MARK':stack.append(mark)
  elif n in ('SHORT_BINUNICODE','BINUNICODE','BINUNICODE8','BININT','BININT1','BININT2','SHORT_BINBYTES','BINBYTES','BYTEARRAY8','BINFLOAT'):stack.append(arg)
  elif n=='NONE':stack.append(None)
  elif n in ('NEWTRUE','NEWFALSE'):stack.append(n=='NEWTRUE')
  elif n=='EMPTY_TUPLE':stack.append(())
  elif n=='EMPTY_LIST':stack.append([])
  elif n=='EMPTY_DICT':stack.append({})
  elif n=='MEMOIZE':memo[len(memo)]=stack[-1]
  elif n in ('BINGET','LONG_BINGET'):stack.append(memo[arg])
  elif n=='STACK_GLOBAL':
   name=stack.pop();module=stack.pop();stack.append(node('global',module,name))
  elif n in ('REDUCE','NEWOBJ'):
   args=stack.pop();fn=stack.pop();stack.append(node(n,fn,args))
  elif n=='BUILD':
   state=stack.pop();stack[-1]['state']=state
  elif n=='TUPLE':stack.append(tuple(popmark()))
  elif n in ('TUPLE1','TUPLE2','TUPLE3'):
   k=int(n[-1]);v=stack[-k:];del stack[-k:];stack.append(tuple(v))
  elif n=='APPENDS':
   v=popmark();stack[-1].extend(v)
  elif n=='SETITEMS':
   v=popmark();stack[-1].update(zip(v[::2],v[1::2]))
  elif n=='SETITEM':
   value=stack.pop();key=stack.pop();stack[-1][key]=value
  elif n=='STOP':break
  else:raise ValueError('Unsupported nonexecuting opcode '+n)
root=stack[0];mgr=root['state']['_mgr']
def brief(x):
 if isinstance(x,(bytes,bytearray)):return {'bytes':len(x)}
 if isinstance(x,list):return {'list_length':len(x),'head':[brief(a) for a in x[:12]]}
 if isinstance(x,tuple):return [brief(a) for a in x]
 if isinstance(x,dict):return {k:brief(v) for k,v in x.items()}
 return x
# Raw symbolic values stay in process; never export per-row behavior.
print('Symbolic decode only; no classes instantiated or callables invoked')
import struct,ast,hashlib,math,statistics,collections
blocks,axes=mgr['args'][1]
def array(x):
 fn,args=x['args'];name=fn['args'][-1]
 if name=='_frombuffer':
  raw,dtype,shape,order=args;assert order=='C';code=dtype['args'][1][0];fmt={'f8':'d','i4':'i','i8':'q'}[code];return [v[0] for v in struct.iter_unpack('<'+fmt,raw)],shape
 state=x['state'];return state[4],state[1]
columns,_=array(axes[0]['args'][1][1]['data']);data={}
for block in blocks:
 values,placement,ndim=block['args'][1];vals,shape=array(values);start,stop,step=placement['args'][1];assert shape[0]==len(range(start,stop,step))
 for j,i in enumerate(range(start,stop,step)):data[columns[i]]=vals[j*shape[1]:(j+1)*shape[1]]
N=len(data['is_fraud']);seq=[];bad=[];tokens=collections.Counter();empty=0;alignment=0;negative_intervals=0
for i,(a,t) in enumerate(zip(data['actions'],data['times'])):
 try:
  if len(a)>2000000 or len(t)>2000000:raise ValueError('literal size budget')
  aa=ast.literal_eval(a);tt=ast.literal_eval(t);assert isinstance(aa,list) and isinstance(tt,list)
  ids=[v for group in aa for v in group];assert all(type(x)==int for x in ids);seq.append(len(ids));tokens.update(ids);empty+=not ids;alignment+=len(aa)!=len(tt);negative_intervals+=sum(v<0 for v in tt)
 except (ValueError,SyntaxError,AssertionError,TypeError):bad.append(i)
duplicates=collections.Counter(hashlib.sha256(repr(tuple(data[c][i] for c in columns)).encode()).hexdigest() for i in range(N))
stats={'status':'nonexecuting_symbolic_static_audit','rows':N,'columns':columns,'label_counts':dict(collections.Counter(data['is_fraud'])),'missing':{k:sum(v is None or (isinstance(v,float) and math.isnan(v)) for v in values) for k,values in data.items()},'duplicate_rows_all_released_columns_excluding_index':sum(v-1 for v in duplicates.values()),'sequence_valid_rows':len(seq),'invalid_sequence_or_times_rows':len(bad),'invalid_row_label_counts':dict(collections.Counter(data['is_fraud'][i] for i in bad)),'empty_sequences':empty,'sequence_lengths':{'min':min(seq),'median':statistics.median(seq),'max':max(seq),'mean':statistics.mean(seq)},'observed_event_ids':len(tokens),'event_id_min':min(tokens),'event_id_max':max(tokens),'event_count':sum(tokens.values()),'nested_actions_times_length_mismatches':alignment,'negative_intervals':negative_intervals,'user_id_released':False,'absolute_transaction_timestamp_released':False,'unpickled':False,'GLOBAL_or_REDUCE_targets_invoked':False}
(r/'row-audit-03.json').write_text(json.dumps(stats,indent=2),encoding='utf8');print(json.dumps(stats,indent=2))
