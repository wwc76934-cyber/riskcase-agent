"""Replay saved responses against repaired host; never a new model-quality measurement."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from app.evaluation.engine import execute
from app.cli.agent_evaluation import summarize,render,sha
from app.service.runtime import write_json


def main():
    root=Path(__file__).resolve().parents[1];bundle=root/'runs/stage4-freeze-v1';source=root/'runs/stage4-live-v1'
    output=root/'runs/stage4-host-replay-v2';output.mkdir(exist_ok=False)
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    assert sha(bundle/'scenarios.json')==manifest['scenarios_sha256'] and sha(bundle/'knowledge.json')==manifest['knowledge_sha256']
    cases={c['id']:c for c in json.loads((bundle/'scenarios.json').read_text(encoding='utf-8'))}
    knowledge=json.loads((bundle/'knowledge.json').read_text(encoding='utf-8'))
    records=json.loads((source/'results.json').read_text(encoding='utf-8'));rows=[];hashes={}
    for record in records:
        path=source/record['case']/record['arm'];files=sorted(path.glob('response-*.json'))
        values=[json.loads(p.read_text(encoding='utf-8')) for p in files]
        for p in files:hashes[str(p.relative_to(root))]=sha(p)
        class Replay:
            simulation=True
            index=0
            def chat(self,*args,**kwargs):
                if self.index>=len(values):raise ValueError('recorded_response_stream_exhausted')
                result=deepcopy(values[self.index]);self.index+=1;return result
        # The factory argument here selects transport; Replay remains a simulation.
        # Use a script-only constructor hook, never instantiate SiliconFlowClient.
        import app.evaluation.engine as engine
        prior_skill,prior_basic=engine.SkillScriptedClient,engine.ScriptedInvestigator
        try:
            engine.SkillScriptedClient=Replay;engine.ScriptedInvestigator=lambda **kwargs:Replay()
            row=execute(cases[record['case']],record['arm'],output/record['case']/record['arm'],knowledge=knowledge)
        finally:engine.SkillScriptedClient,engine.ScriptedInvestigator=prior_skill,prior_basic
        row['client_kind']='saved_response_replay_NOT_NEW_MODEL';row['original_passed']=record['score']['task_passed']
        rows.append(row);write_json(output/'results.json',rows)
    write_json(output/'summary.json',summarize(rows));render(rows,output)
    write_json(output/'provenance.json',dict(source_response_sha256=hashes,host_files_sha256={str(p.relative_to(root)):sha(p) for p in (root/'app/investigation').glob('*.py')},
        contract='evidence-v2-plans-advisory',fresh_model_requests=0,changed_context_not_observed_by_model=True,
        limits=['Saved model responses ignore new prompt; this tests host behavior only','Original results unchanged','Cannot infer fresh-model completion rate']))
    print(json.dumps(summarize(rows),ensure_ascii=False))


if __name__=='__main__':main()
