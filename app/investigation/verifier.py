"""Deterministic entailment gate for the explicitly versioned research policy only.

This does not judge arbitrary prose or real fraud. It never reads benchmark gold
or unretrieved evidence and never changes a model conclusion to make it pass.
"""


def supported_verdict(answer, evidence):
    cited=[evidence[key]['result'] for key in answer['evidence_ids']]

    def unique(source):
        rows=[r['data'] for r in cited if r['source']==source]
        if not rows: raise ValueError('Required original absent: '+source)
        if any(r!=rows[0] for r in rows): raise ValueError('Conflicting originals: '+source)
        return rows[0]

    procedure=unique('procedure')
    if procedure.get('rule_version')!='research-v1': raise ValueError('Unsupported policy version')
    overview=unique('transactions')
    if type(overview.get('history_complete')) is not bool or type(overview.get('repeated_authorization')) is not bool:
        raise ValueError('Invalid transaction facts')
    if not overview['history_complete']:
        expected='insufficient_evidence'
    elif overview['repeated_authorization']:
        settlement=unique('settlement')
        if type(settlement.get('complete')) is not bool: raise ValueError('Invalid settlement coverage')
        if not settlement['complete']:
            expected='insufficient_evidence'
        else:
            count=settlement.get('settled_debits')
            if type(count) is not int or count<1: raise ValueError('Invalid settled debit count')
            expected='duplicate_record' if count==1 else 'needs_review'
    else:
        merchant=unique('merchant')
        if merchant.get('claim')!='business_change': raise ValueError('Unsupported merchant claim')
        empty_search=any(r['source']=='catalog' and r['data'].get('kind')=='disposition' and r['data'].get('hits')==[] for r in cited)
        dispositions=[r for r in cited if r['source']=='disposition']
        if empty_search and not dispositions:
            expected='insufficient_evidence'
        else:
            disposition=unique('disposition')
            if empty_search: raise ValueError('Conflicting existence evidence')
            status=disposition.get('status')
            if status not in ('confirmed','denied'): raise ValueError('Unsupported disposition status')
            expected='normal_change' if status=='confirmed' else 'needs_review'
    if answer['verdict']!=expected: raise ValueError('Conclusion contradicts cited research policy and facts')
    return dict(policy='research-v1', structured_conclusion_supported=True,
                free_text_entailment='requires_review', fraud_decision=False)
