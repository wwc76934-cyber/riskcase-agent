"""Non-LLM baselines: a keyword rule engine and a supervised character n-gram classifier."""
import re

MARKERS = ["安全账户", "资金核查", "验证码", "保证金", "手续费", "解冻", "稳赚", "保本", "返利", "返佣", "刷单", "垫付", "公安", "检察院",
           "法院", "涉嫌", "洗钱", "冻结", "保密", "不要告诉", "共享屏幕", "远程", "链接", "点击", "密码", "导师", "带单", "内部消息",
           "私人账户", "个人账户", "立即", "马上", "最后一天", "否则"]
INTERCEPT_AT = 3  # distinct markers


def rules_predict(text):
    hits = [m for m in MARKERS if m in text]
    return dict(is_fraud=len(hits) >= INTERCEPT_AT, hits=hits)


class TfidfLR:
    def __init__(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        self.vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), min_df=2, sublinear_tf=True)
        self.clf = LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced")
        self.type_clf = LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced")

    def fit(self, texts, labels, types):
        X = self.vec.fit_transform(texts)
        self.clf.fit(X, [int(l == "fraud") for l in labels])
        idx = [i for i, l in enumerate(labels) if l == "fraud"]
        self.type_clf.fit(X[idx], [types[i] for i in idx])
        return self

    def predict(self, texts):
        X = self.vec.transform(texts)
        proba = self.clf.predict_proba(X)[:, 1]
        ftypes = self.type_clf.predict(X)
        return [dict(is_fraud=bool(p >= 0.5), proba=float(p), fraud_type=t if p >= 0.5 else "none") for p, t in zip(proba, ftypes)]
