"""Static measured-result charts. No training or source transaction export."""
import argparse
import json
from pathlib import Path


def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault('MPLCONFIGDIR', str(Path('runs/semantic-plot-cache').resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    report = json.loads(args.summary.read_text(encoding='utf-8'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    names = list(report['e1'])
    values = [report['e1'][name]['ensemble']['tp_daily10'] for name in names]
    axes[0].bar(names, values, color=['#8394a5', '#51729c', '#2a8677', '#ce8540', '#b3a398'], label='Mean-score ensemble')
    for i, name in enumerate(names):
        seeds = report['e1'][name]['seed_tp']
        axes[0].scatter([i - .08, i, i + .08], seeds, color='#223340', marker='x', zorder=3,
                        label='Individual seed' if i == 0 else None)
        axes[0].text(i + .23, values[i] + 3, str(values[i]), ha='center')
    frozen = report['historical_controls']['frozen_residual']['metrics']['tp_daily10']
    axes[0].axhline(frozen, color='#b54646', linestyle='--', label=f'Frozen residual (no channel): {frozen}')
    axes[0].set(title='E1: additive representation, fixed 5 epochs', ylabel='Positive labels captured / daily top10')
    axes[0].tick_params(axis='x', labelrotation=15)
    axes[0].legend(fontsize=8)
    fractions = sorted(report['e3'], key=float)
    labels = {'bce': 'Weighted BCE', 'focal': 'Focal', 'cb_focal': 'CB-Focal',
              'consistency': 'CB-Focal + consistency', 'semantic_consistency': 'Semantic + consistency'}
    for name, label in labels.items():
        axes[1].plot([100 * float(x) for x in fractions],
                     [report['e3'][x]['conditions'][name]['ensemble']['tp_daily10'] for x in fractions],
                     marker='o', label=label)
    axes[1].set(xscale='log', xticks=[5, 10, 20, 100], xticklabels=['5%', '10%', '20%', '100%'],
                xlabel='Exposed training labels', ylabel='Positive labels captured / daily top10',
                title='E3: bounded 2-epoch label-budget study')
    axes[1].legend(fontsize=8)
    for ax in axes:
        ax.grid(axis='y', alpha=.2)
    fig.suptitle('IBM synthetic source | observed 2018 development | no new independent test', fontsize=12)
    fig.savefig(args.output, dpi=180)
    plt.close(fig)


if __name__ == '__main__':
    main()
