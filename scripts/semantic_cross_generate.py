"""Generate the cross-context minimal pairs (U1): a customer statement plus structured facts.

    python -m scripts.semantic_cross_generate <generator_model> [--cap CNY]

Four categories, ten pairs each. In a pair the statement is identical (A, C), nearly identical (B) or shares
its first turn (D); the facts or the follow-up decide the label. Written by a model from another family than
the evaluated ones, from written specs. Output: data/semantic/cross_context_pairs.jsonl (two rows per pair).
"""
import argparse
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

from app.semantic.benchmark import read_jsonl
from app.semantic.llm import LLM, Budget, BudgetExceeded, LLMUnavailable, parse_json

OUT = Path("data/semantic/cross_context_pairs.jsonl")
SYSTEM = ("你是银行风控评测团队的数据构造助手，为检测系统写出逼真的中文评测用例。只输出一个 JSON 对象，不要输出别的内容。"
          "不得包含真实姓名、电话或账号。")
SEEDS = {
    "alibi_telemetry_conflict": ["深夜在外地的境外商户刷卡", "电商大额数码订单", "跨城珠宝店消费", "海外酒店预订", "加油站连续刷卡",
                                 "网约车异地消费", "演唱会门票", "健身房年卡", "奢侈品代购", "线下超市大额购物"],
    "coerced_app_scam_vs_genuine": ["客户转账给陌生账户", "客户清空定期存款", "客户为所谓投资老师转账", "客户给网恋对象转账", "客户转账缴纳所谓解冻费",
                                    "客户给装修公司付尾款", "客户给亲戚汇买房款", "客户给大学生子女汇学费", "客户给房东转租金", "客户给供应商付货款"],
    "pragmatic_scope_and_quoted_denial": ["卡片遗失后部分交易", "手机被盗后的几笔扣款", "亲友代刷卡的一段时间", "出差期间的海外消费", "家人借用银行卡的一天",
                                          "订阅服务自动续费", "酒店押金与消费分开", "分期付款和一次性付款", "旧手机解绑前后", "挂失前后几小时"],
    "multi_turn_self_contradiction": ["称卡从未外借", "称一直在本地", "称从未使用过该商户", "称收货地址不是自己的", "称没有收到商品",
                                      "称当时手机关机", "称从没绑定过该设备", "称没有授权家人使用", "称不认识收款人", "称这笔订单从未下过"],
}

SEEDS_B = {
    "alibi_telemetry_conflict": ["高铁站旁的电子产品店", "凌晨的网络游戏充值", "异地加油站", "机场免税店", "景区酒店", "海外购物网站", "二手车订金", "旅游平台机票", "医美机构", "线上课程平台"],
    "coerced_app_scam_vs_genuine": ["客户转账给陌生个人账户做所谓保证金", "客户抵押房产后转账", "客户为了所谓高额返利转账", "客户给孩子的留学中介付款", "客户给已知的装修队付定金",
                                    "客户给姐姐转买房首付", "客户给新认识的导师缴费", "客户给医院缴费", "客户给公司账户付货款", "客户给所谓客服转退款手续费"],
    "pragmatic_scope_and_quoted_denial": ["卡片被同事借用一段时间", "家人在外地替自己刷卡", "手机丢失后的几笔支付", "代购人员使用卡片", "换卡前后的自动扣款",
                                          "外出旅行期间的几次消费", "托管商户的预授权", "酒店退房前后的扣费", "试用期转正后的订阅", "更换手机号前后的验证"],
    "multi_turn_self_contradiction": ["称没去过那个城市", "称没用过那个支付账户", "称卡一直在家", "称不认识那个商户", "称从未登录过那个应用",
                                      "称没有在那个时间上网", "称没有委托别人取款", "称没有收到验证码", "称没有改过预留手机号", "称从未使用过那台设备"],
}
LEAK_FREE = ("事实字段要像真实系统的原始字段：用数值、代码、时间戳、布尔值、枚举编码表示（例如 device_trust_score、ble_peer_id、"
             "txn_to_home_km、biometric_flag、ship_addr_match_history 等），不要用“本人常用”“与声明矛盾”“可疑”“符合”这类直接点明结论的措辞。"
             "两份事实里决定性字段的取值差别要靠数值或编码体现，其余字段尽量相同。\n")
SPECS = {
    "alibi_telemetry_conflict": (
        "类别：时空与设备不在场证明冲突。客户说自己没有做过这笔交易（委婉说法，例如在某处休息、手机一直在身边、卡在钱包里）。"
        "给出同一份客户声明，以及两份结构化客观事实：facts_genuine（真实被盗刷，事实支持客户的说法）和 facts_false（客户在虚假申诉，"
        "事实里藏有一两处与声明逻辑互斥的细节，比如交易使用客户本人常用且绑定了生物识别的设备，或连接了本人的车载蓝牙，或发货到本人历史常用地址）。"
        "两份事实的键相同，只在决定性的键上取值不同。\n"
        "输出 JSON：{\"statement\":\"客户声明，1到3句\",\"facts_genuine\":{...},\"facts_false\":{...},\"decisive_keys\":[\"facts_false 里决定性的键\"],\"edit\":\"一句话说明差别\"}。"),
    "coerced_app_scam_vs_genuine": (
        "类别：本人授权下的受胁迫或被洗脑的转账。两段客户与对方（银行柜员或客服）的对话，客户在两段里都说“我是自愿转的，不要拦截”。"
        "dialogue_coerced 里要有语用破绽（改口、机械的公文式措辞、回避和收款人的关系、复述别人教的话）；dialogue_genuine 是真正自愿的、说得清关系和用途。"
        "各配一份 account_context 事实：facts_coerced（例如清空定期、转给异地陌生账户、首次转账）与 facts_genuine（转给历史常联系的亲友或合同对方）。两份事实的键相同。\n"
        "每段对话 4到6句，格式“客户：...\\n对方：...”。\n"
        "输出 JSON：{\"dialogue_coerced\":\"...\",\"dialogue_genuine\":\"...\",\"facts_coerced\":{...},\"facts_genuine\":{...},\"decisive_keys\":[\"facts_coerced 里决定性的键\"],\"edit\":\"一句话说明差别\"}。"),
    "pragmatic_scope_and_quoted_denial": (
        "类别：引述否定与条件时间域切分。客户声明里包含多层引述、条件承认或局部时间段否定，例如“某时刻之前的我认，之后的我不认”“客服说我没同意，其实是我让家人用的”。"
        "同一份声明，配两份事实 facts_contradicted 与 facts_consistent，事实里要有目标交易的时间 target_txn_time 和与声明相关的其他时间点（如挂失时间、借卡时段）。"
        "在 facts_contradicted 里，目标交易落在客户已承认的时间段内却被客户否认（客户的否认被事实推翻）；在 facts_consistent 里，目标交易落在客户否认的时间段内（客户说法成立）。"
        "两份事实必须都包含这些键：target_txn_time（目标交易时间）、admitted_window_start 和 admitted_window_end（客户自己承认使用、借出或授权的时间段）、denied_after（客户声明里“这个时间之后的我不认”的时间点），客户声明要明确说出承认的时间段和否认的时间点。facts_contradicted：target_txn_time 落在承认的时间段内；facts_consistent：target_txn_time 晚于 denied_after 且晚于 admitted_window_end。其余键和取值两份事实保持相同。\n"
        "输出 JSON：{\"statement\":\"客户声明，2到4句\",\"facts_contradicted\":{...},\"facts_consistent\":{...},\"decisive_keys\":[\"target_txn_time\"],\"edit\":\"一句话说明差别\"}。"),
    "multi_turn_self_contradiction": (
        "类别：多轮陈述前后自相矛盾。t0 是客户的首次说法。t1_contradictory 是被追问或看到证据后的补充说法，推翻了 t0 里的关键点（改口称其实借给过别人、其实去过那里等）；"
        "t1_consistent 是补充说法，与 t0 不矛盾，只是补充细节。两个 t1 都要像自然口语。再给一份只提供背景的客观事实 facts，键 4 到 7 个，事实不得与 t0、t1_consistent 的任何一句矛盾；矛盾只来自 t1_contradictory 推翻了 t0。\n"
        "输出 JSON：{\"t0\":\"...\",\"t1_contradictory\":\"...\",\"t1_consistent\":\"...\",\"facts\":{...},\"decisive_keys\":[\"facts 里被追问用到的键\"],\"edit\":\"一句话说明差别\"}。"),
}


def flat(d):
    return isinstance(d, dict) and 4 <= len(d) <= 24 and all(isinstance(k, str) and isinstance(v, (str, int, float, bool)) for k, v in d.items())


def rows_from(category, n, obj):
    """Return the two rows of a pair, or None when the model's JSON does not meet the spec."""
    keys = obj.get("decisive_keys")
    edit = str(obj.get("edit", ""))[:200]
    if category == "alibi_telemetry_conflict":
        stmt = obj.get("statement")
        a, b = obj.get("facts_genuine"), obj.get("facts_false")
        parts = [(stmt, a, "legit"), (stmt, b, "fraud")]
    elif category == "coerced_app_scam_vs_genuine":
        g, c = obj.get("dialogue_genuine"), obj.get("dialogue_coerced")
        parts = [(g, obj.get("facts_genuine"), "legit"), (c, obj.get("facts_coerced"), "fraud")]
        a, b = obj.get("facts_genuine"), obj.get("facts_coerced")
    elif category == "pragmatic_scope_and_quoted_denial":
        stmt = obj.get("statement")
        a, b = obj.get("facts_consistent"), obj.get("facts_contradicted")
        parts = [(stmt, a, "legit"), (stmt, b, "fraud")]
    else:
        t0 = obj.get("t0")
        facts = obj.get("facts")
        a = b = facts
        parts = [(f"T0：{t0}\nT1：{obj.get('t1_consistent')}", facts, "legit"), (f"T0：{t0}\nT1：{obj.get('t1_contradictory')}", facts, "fraud")]
    if not isinstance(keys, list) or not (flat(a) and flat(b)) or any(not isinstance(s, str) or len(s.strip()) < 8 for s, _, _ in parts):
        return None
    if category == "alibi_telemetry_conflict" and set(a) != set(b):
        return None
    if category == "pragmatic_scope_and_quoted_denial" and not ("target_txn_time" in a and "target_txn_time" in b):
        return None
    pid = f"cross-{category}-{n:02d}"
    return [dict(id=f"{pid}-{'contradicted' if label == 'fraud' else 'consistent'}", category=category, pair=pid, label=label,
                 kind="fraud" if label == "fraud" else "near", fraud_type=category if label == "fraud" else None,
                 statement=s.strip(), context_facts=f, decisive_keys=keys, edit=edit, generator="")
            for s, f, label in parts]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--cap", default="14")
    parser.add_argument("--set", choices=["a", "b"], default="a")
    parser.add_argument("--out", default=str(OUT))
    a = parser.parse_args()
    out_path = Path(a.out)
    seeds = SEEDS if a.set == "a" else SEEDS_B
    leak_free = LEAK_FREE if a.set == "b" else ""
    done = {r["id"] for r in read_jsonl(out_path)}
    llm = LLM(Budget("runs/semantic-ledger.sqlite", a.cap))
    lock = threading.Lock()
    jobs = [(c, i) for c in SPECS for i in range(10)]

    def work(job):
        category, i = job
        if f"cross-{category}-{i:02d}-consistent" in done:
            return
        user = f"场景种子：{seeds[category][i]}。\n{SPECS[category]}\n{leak_free}风格要贴近真实银行客服场景，客户说法要可信，不要在声明里直接点明性质。"
        for attempt in range(2):
            try:
                text, _ = llm.chat(a.model, SYSTEM, user, purpose="cross-gen", max_tokens=2400, temperature=0.8 + 0.1 * attempt)
            except (BudgetExceeded, LLMUnavailable):
                return
            rows = rows_from(category, i, parse_json(text) or {})
            if rows:
                with lock, out_path.open("a", encoding="utf-8") as handle:
                    for r in rows:
                        r["generator"] = a.model
                        handle.write(json.dumps(r, ensure_ascii=False) + "\n")
                return

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(work, jobs))
    print("pairs", len(read_jsonl(out_path)) // 2)


if __name__ == "__main__":
    main()
