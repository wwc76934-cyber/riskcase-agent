"""Held-out evaluation cases written after the Policy Guard table was frozen.

Twelve AI-authored simulated cases in the same record format as the 40-case set.
Six use paraphrase, quotation, partial denial, late correction or hedging that
the keyword rules in the deterministic workflow (B0) do not cover. Six use plain
wording. No independent person reviewed the labels.

Ground truth is the pair (primary_state, final_state) in each tuple; the reference
is derived from it by the same generator as the 40-case set.
"""
from collections import Counter
import hashlib
from pathlib import Path

from app.evaluation.v3_tasks import write_new
from app.evaluation.v31_query_benchmark import query_case
from app.investigation.contracts import digest

# family, name, route, primary text, fallback text, corroborating original,
# later statement, primary outcome, primary state, follow-up, final state
HELDOUT = (
    # ---- semantic cases ----
    ('indirect_denial', 'h01_child_phone',
     '登记号码回呼可核对谁在操作账户。回呼无人接听时，改用应用内二次核验。',
     '本人接听。{P} 这笔是孩子拿着我手机点出去的，我当时在洗澡，完全不知情。',
     '应用内二次核验。{P} 的支付我事先毫不知情，是孩子误点的。',
     '设备事件：{P} 发生时设备处于儿童模式登录，指纹验证次数为零；{Q} 由本人指纹验证。',
     '回看记录后我确认 {P} 与我无关，{Q} 才是我自己付的。',
     'available', 'denied', 'corroborate', 'denied'),
    ('quoted_denial', 'h02_quoted_denial',
     '回呼先让客户复述客服转述的说法，再确认本人立场。',
     '客服通知我说“你没同意 {P}”，其实是我让朋友代付的，这笔我认。',
     '应用核验：朋友代付 {P} 是我交代的，我认可。',
     '代付人备注：{P} 由持卡人口头委托，付款人称“他让我先垫”。',
     '再次确认，{P} 是我委托朋友付的。',
     'available', 'authorized', 'corroborate', 'authorized'),
    ('conditional_split', 'h03_split_by_time',
     '先听客户说明认可的是哪一笔，再决定是否核对终端日志。',
     '下午那笔 {Q} 我认；晚上这笔 {P} 我不认。',
     '应用核验：{P} 我从来没点过确认。',
     '终端日志：{Q} 带本人指纹，{P} 为密码登录且设备为新设备。',
     '补充：{P} 不属于我授权的范围，{Q} 才是。',
     'available', 'denied', 'corroborate', 'denied'),
    ('late_retraction', 'h04_late_retraction',
     '回呼后若客户改口，查订单元数据交叉核对。',
     '我先报了异常，{P} 不是我做的。',
     '应用核验：我核对后确认 {P} 是我自己买的。',
     '订单元数据：{P} 的收货地址和下单设备都是客户常用的。',
     '我对着订单截图想起来了，{P} 是我自己买的，之前报错了，请撤销异常。',
     'available', 'denied', 'corroborate', 'authorized'),
    ('friendly_fraud', 'h05_friendly_fraud',
     '客户称未收货。先听陈述，再查物流签收。',
     '我从没收到这个商品，{P} 我拒付。',
     '应用核验：我查了一下，货是我家人签收的，{P} 的付款我同意。',
     '物流签收：{P} 订单由持卡人登记地址签收，签收人为同住家属，附签收照片。',
     '确认：{P} 的货已收到，付款是我本人同意的，之前申诉有误。',
     'available', 'denied', 'corroborate', 'authorized'),
    ('hedged_statement', 'h06_hedged',
     '客户不确定时不要替客户下结论，先核对其他来源。',
     '应该是我爱人付的，我不太确定，{P} 我没法说清是不是我同意的。',
     '应用核验：我先问问家里人，现在说不准 {P}。',
     '共同账户成员登记：本账户有两名可操作成员，{P} 的操作人未记录。',
     '问过家人后，{P} 不是我们家任何人付的，我没有同意。',
     'available', 'unresolved', 'corroborate', 'denied'),
    # ---- plain wording ----
    ('plain_wording', 'h07_plain_denied',
     '登记号码回呼；回呼无响应则用应用核验。',
     '回呼原件未归档，本段不会返回。',
     '应用核验：我没有同意 {P}，我同意的是 {Q}。',
     '终端登录事件，没有持卡人签名，无法证明 {P} 付款许可。',
     '再次说明：我没有授权 {P}。',
     'unavailable', 'unresolved', 'fallback', 'denied'),
    ('plain_wording', 'h08_plain_authorized',
     '回呼核对订购意向，再看商户订单。',
     '本人确认：{P} 是我同意的订购。',
     '应用核验：我同意 {P}。',
     '商户订单：{P} 与本人常用邮箱下单记录一致。',
     '重申：{P} 我同意。',
     'available', 'authorized', 'corroborate', 'authorized'),
    ('plain_wording', 'h09_plain_pair',
     '回呼时分别确认两笔扣款。',
     '我同意 {Q}，没有同意 {P}。',
     '应用核验：我没有同意 {P}。',
     '商户记录：{P} 与 {Q} 分属不同订单，{P} 无本人确认。',
     '我没有授权 {P}。',
     'available', 'denied', 'corroborate', 'denied'),
    ('plain_wording', 'h10_plain_unavail_auth',
     '先尝试旧渠道回呼；不可用时发起应用核验。',
     '旧渠道原件不可用，本段不会返回。',
     '应用核验：{P} 是我同意的续费。',
     '续费记录：{P} 与既有订阅周期一致。',
     '再次确认 {P} 是我同意的。',
     'unavailable', 'unresolved', 'fallback', 'authorized'),
    ('plain_wording', 'h11_plain_revoked',
     '回呼后查撤回通知送达日志。',
     '我在付款前已经取消，我没有许可 {P}。',
     '应用核验：我没有许可 {P}。',
     '送达日志：撤回通知接收时间早于 {P}，之后没有重新授权记录。',
     '我没有授权 {P}。',
     'available', 'denied', 'corroborate', 'denied'),
    ('plain_wording', 'h12_plain_correction',
     '回呼后若陈述前后不一致，保留时间线。',
     '我同意 {P}。',
     '应用核验：我没有同意 {P}。',
     '通话摘要：客户先后两次陈述不一致。',
     '更正：我没有同意 {P}，上一通电话说错了。',
     'available', 'authorized', 'corroborate', 'denied'),
)

# The fallback channel defaults to the final state; this case needs it to stay unresolved.
EXTRA_FALLBACK_STATES = {'h06_hedged': 'unresolved'}
SEMANTIC = tuple(entry[1] for entry in HELDOUT[:6])
ORDINAL_BASE = 50


def candidates():
    for offset, entry in enumerate(HELDOUT):
        yield query_case(entry, ORDINAL_BASE + offset, EXTRA_FALLBACK_STATES)


def build(output):
    output = Path(output)
    cases = list(candidates())
    output.mkdir(parents=True, exist_ok=False)
    (output / 'runtime').mkdir()
    (output / 'controller').mkdir()
    entries = []
    for snapshot, reference in cases:
        case_id = snapshot['case_id']
        write_new(output / 'runtime' / (case_id + '.json'), snapshot)
        write_new(output / 'controller' / (case_id + '.json'), reference)
        entries.append(dict(case_id=case_id, family=reference['family'], semantic=case_id[len('v31-eval-'):] in SEMANTIC,
                            runtime_digest=digest(snapshot), reference_digest=digest(reference)))
    manifest = dict(schema='v4-heldout-candidates-v1', cases=entries, counts=dict(Counter(e['semantic'] for e in entries)),
                    review_scope='AI_author_reviewed_simulation', independent_person_reviewed=False)
    write_new(output / 'dataset.json', manifest)
    write_new(output / 'dataset-sha256.json',
              dict(sha256=hashlib.sha256((output / 'dataset.json').read_bytes()).hexdigest()))
    return manifest
