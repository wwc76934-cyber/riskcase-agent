"""A second, harder hand-written contrast set (10 pairs).

Written after the first contrast set had been scored, so it is not blind to that result. It targets what the
first set did not: scams with no typical scam vocabulary, and legitimate exchanges that use scam vocabulary
or ask the customer probing questions. Scored once per model, reported separately.
"""
from app.semantic.taxonomy import FRAUD_TYPES

PAIRS = [
    ("h01", "authority",
     ["对方：银行柜员问我这笔转账是做什么用的。", "客户：我该怎么回答？"],
     ["对方：照我教你的说，就说是买房首付，别提我们电话联系的事。转完我这边会确认。"],
     ["对方：如实说就行，是买房首付的话，把购房合同带上给柜员看，需要的话我陪您一起去柜台。"],
     [], "教客户对银行撒谎 / 如实说明并带合同"),
    ("h02", "authority",
     ["客户：这笔税款我该怎么缴？"],
     ["对方：要用礼品卡缴纳，您去买好后把刮开的卡号拍照发给我，今天内有效。"],
     ["对方：请通过税务官方应用或银行柜台缴纳，我们不接受礼品卡，也不会通过电话收款。"],
     [], "用礼品卡缴税 / 官方应用缴税"),
    ("h03", "romance",
     ["对方：我最近手头有点紧。"],
     ["对方：你先借我三万，我回国就还，我现在不方便视频，也别跟你朋友说我们的事。"],
     ["对方：你上次说可以借我三万应急，我给你写了借条，周五发工资就先还一半，我们当面交接。"],
     [], "不能视频且要求保密 / 有借条并当面交接"),
    ("h04", "authority",
     ["柜员：您这笔转账有人让您保密或者让您转到安全账户吗？"],
     ["客户：不是别人让的……是警官说查完会还我，他让我别多说，也别跟你们讲。"],
     ["客户：没有，是我自己要付装修尾款，合同和对方的公司账户信息都在这里。"],
     ["柜员：好的，我们再核对一下收款方信息。"], "客户承认有人指导并保密 / 自主付款并出示合同"),
    ("h05", "loan_fee",
     ["客户：房租今天要交，怎么转？"],
     ["对方：房东换了新账户，是他朋友的名字，合同上的账户作废了，你今天不转就被别人租走了。"],
     ["对方：收款账户就是合同上写的房东本人名下的卡，您可以对照合同页，今天内转就行。"],
     [], "改成第三方新账户并催促 / 合同上的房东本人账户"),
    ("h06", "investment",
     ["客户：我想让你帮我打理这笔钱。"],
     ["对方：放心，亏了算我的。你把钱转到这个账户，我帮你操作，不用登录，也不用告诉家人。"],
     ["对方：钱放在您自己的账户里，操作由您自己确认，亏损您自己承担，我只给您讲解产品。"],
     [], "转入对方账户代操作并保本 / 资金留在本人账户自己操作"),
    ("h07", "service_refund",
     ["客户：我的订单要退款。"],
     ["对方：请您先向这个账户转一元验证身份，转完我们才能把退款打给您。"],
     ["对方：退款已提交，会自动原路退回，您不需要做任何操作。"],
     [], "要求先转一元验证 / 自动原路退回无需操作"),
    ("h08", "loan_fee",
     ["客户：我想提高信用卡额度。"],
     ["对方：需要您先转三千元到这个账户做流水，才能通过系统审核。"],
     ["对方：提额需要提交收入证明，我们会在系统里审核，您不需要向任何账户转账。"],
     [], "转账做流水 / 提交收入证明"),
    ("h09", "phishing",
     ["对方：您好。"],
     ["对方：您的快递丢失，请加客服微信，在对话里填写银行卡号和手机上收到的验证码办理理赔。"],
     ["对方：您的快递已到驿站，取件码6688，请今天内取走，如有问题请联系站点电话。"],
     [], "填写卡号和验证码理赔 / 取件码通知"),
    ("h10", "authority",
     ["对方：关于防范电信诈骗，我们这边通知您。"],
     ["对方：为防止资金被冻结，请先把存款转入国家监管账户保管，我们帮您看管，不要告诉别人。"],
     ["对方：社区下周六上午有一场反诈宣传课，不需要缴费，也不需要提供任何账户信息，欢迎参加。"],
     [], "转入监管账户保管并保密 / 免费宣传课通知"),
]


def build():
    rows = []
    for pid, ftype, head, scam, legit, tail, edit in PAIRS:
        assert ftype in FRAUD_TYPES
        for label, middle in (("fraud", scam), ("legit", legit)):
            rows.append(dict(id=f"hard-{pid}-{label}", split="contrast_hard", kind="fraud" if label == "fraud" else "near",
                             key=ftype, label=label, fraud_type=ftype if label == "fraud" else None, pair=pid, edit=edit,
                             text="\n".join(head + middle + tail), truth_signals=[], generator="human-author"))
    return rows
