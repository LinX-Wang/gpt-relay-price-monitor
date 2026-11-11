def classify(report):
    f=report.get('fingerprint') or {}
    winner=f.get('model')
    claimed=report.get('claimed_model') or f.get('claimed_model')
    if report.get('operational_status') in ('prepared','running','stopping'):
        return {'color':'running','label':'检测中','accepted':None}
    if not winner or f.get('quality_status')!='sufficient' or f.get('verdict') not in ('match','mismatch'):
        return {'color':'yellow','label':'证据不足 · 待确认','accepted':None}
    accepted=winner==claimed or (claimed=='gpt-6.1-sol' and winner in ('gpt-6.1-sol','gpt-6-astra'))
    return {'color':'green' if accepted else 'red','label':('好结果 · 指向 ' if accepted else '未符合接受规则 · 指向 ')+winner,'accepted':accepted,'selected_model':winner,'rule':'sol-or-astra-v1'}
