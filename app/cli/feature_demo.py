"""A small Chinese walkthrough of history features, using invented records."""

from datetime import datetime

from app.risk.features import FeatureTransaction, iter_history_features


def main():
    def tx(identity, clock, amount, merchant):
        return FeatureTransaction(identity, "A", datetime.fromisoformat(f"2016-02-01T{clock}:00"),
                                  amount * 100, merchant, "教学渠道", "教学类别", "train")
    rows = [tx("past-1", "09:00", 20, "商户甲"), tx("past-2", "09:40", 30, "商户乙"),
            tx("current", "10:00", 100, "商户乙"), tx("same-time", "10:00", 500, "商户丙"),
            tx("future", "10:10", 900, "商户乙")]
    result = next(row for row in iter_history_features(rows, coverage_start=datetime(2016, 1, 1))
                  if row["transaction_id"] == "current")
    print("这是自建教学例子，不是真实客户数据。金额用作教学单位。")
    print("\n正在判断：10:00，在商户乙消费 100。")
    print("此前交易：09:00 消费 20；09:40 消费 30。")
    print("另有 10:00 同刻消费 500、10:10 消费 900，均不能算进这笔的过去。")
    print(f"\n程序算出：过去一小时 {result['count_1h']} 笔。")
    print(f"程序算出：过去一天金额净和 {result['sum_signed_cents_24h'] / 100:g}。")
    print(f"程序算出：过去 30 天见过商户乙：{'是' if result['merchant_seen_30d'] else '否'}。")
    print("\n这几个数字就是特征：给检测模型的线索，还不是欺诈结论。")


if __name__ == "__main__":
    main()
