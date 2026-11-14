"""Offline, explicitly scripted procedure-memory demo and retrieval evaluation."""
import argparse
import json
from pathlib import Path

from app.tools.knowledge_tools import KnowledgeStore


def demo(directory):
    directory.mkdir(parents=True, exist_ok=False)
    store = KnowledgeStore(directory/'knowledge.sqlite')
    for identity, body in [
        ('coverage', '频次不足时检查历史覆盖；覆盖不完整则资料不足，不能宣称没有风险。'),
        ('amount', '金额新高检查只比较正金额的历史最大值；负金额不是正向消费。'),
        ('merchant', '新商户需要完整历史窗口与非空历史；商户ID不是现实门店的核实身份。')]:
        store.add_reviewed(scope='synthetic-demo', identity=identity, version=1,
            available_at='2020-01-01T00:00:00+00:00', kind='procedure', body=body,
            source='hand_authored_retrieval_fixture_not_business_policy', reviewer='fixture-author')
    # This tiny fixture checks mechanisms, not independent generalization.
    questions = [('频次不足历史覆盖', 'coverage'), ('正金额历史最大值', 'amount'),
        ('新商户完整历史窗口', 'merchant'), ('天气预报', None),
        ('velocity baseline incomplete', 'coverage')]
    results = []
    for question, expected in questions:
        hits = store.search(question, scope='synthetic-demo', as_of='2020-02-01T00:00:00+00:00')
        found = [h['citation'].split(':')[1] for h in hits]
        results.append(dict(question=question, expected=expected, retrieved=found,
            passed=(expected in found if expected else not found)))
    answerable = [r for r in results if r['expected']]
    report = dict(data_kind='hand_authored_mechanism_fixture', real_llm_used=False,
        recall_at_3=sum(r['passed'] for r in answerable)/len(answerable),
        cases=results, limits=['Questions overlap corpus; not an independent benchmark.',
            'English synonym query intentionally exposes lexical retrieval weakness.',
            'No task accuracy or memory improvement claim follows from retrieval recall.'])
    (directory/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    demo(parser.parse_args().output)


if __name__ == '__main__':
    main()
