"""Eight invented teaching rows: show one CPU network learning a simple pattern."""

import numpy as np
from rich.console import Console
from rich.table import Table
import torch

from app.risk.neural_model import RiskMLP, make_loader, make_loss, predict_batches, seed_training, train_one_epoch


def main():
    seed_training(42, 1)
    X = np.array([[-2,-1],[-1,-2],[-1,-1],[-2,-2],[1,1],[1,2],[2,1],[2,2]], dtype=np.float32)
    y = np.array([0,0,0,0,1,1,1,1], dtype=np.int8)
    model = RiskMLP(2, (8, 4), 0)
    criterion, ratio = make_loss(y)
    loader = make_loader(X, y, batch_size=8)
    before, before_loss = predict_batches(model, loader, criterion)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.03)
    for _ in range(40):
        stats = train_one_epoch(model, loader, optimizer, criterion)
    after, after_loss = predict_batches(model, loader, criterion)
    table = Table(title="8 条人为编造的教学数据，不是实际交易成绩")
    for column in ("输入线索", "训练答案", "学习前分数", "学习后分数"):
        table.add_column(column)
    for values, label, start, end in zip(X, y, before, after):
        table.add_row(str(values.tolist()), str(label), f"{start:.4f}", f"{end:.4f}")
    console = Console()
    console.print(table)
    console.print(f"损失：{before_loss:.4f} → {after_loss:.4f}；训练 40 轮，每轮 1 批；正例权重 {ratio:.1f}")
    console.print(f"最后一轮梯度范数 {stats['maximum_gradient_norm']:.4f}：梯度用于指导参数调整。")
    console.print("训练：清梯度 → 前向计算 → 算损失 → backward → step。验证：eval + no_grad，不更新参数。")


if __name__ == "__main__":
    main()
