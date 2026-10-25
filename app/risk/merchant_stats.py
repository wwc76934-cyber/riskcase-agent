"""User-local merchant concentration; record counts and positive amounts differ."""

from collections import Counter


def merchant_distribution(rows):
    counts,amounts,positive_counts = Counter(),Counter(),Counter()
    negative_count = zero_count = negative_sum = 0
    for row in rows:
        merchant,amount = row["merchant_id"],row["amount_cents"]
        if not isinstance(merchant,str) or not merchant or type(amount) is not int: raise ValueError("Merchant ID and integer amount required")
        counts[merchant] += 1
        if amount>0:
            amounts[merchant] += amount; positive_counts[merchant] += 1
        elif amount<0:
            negative_count += 1; negative_sum += amount
        else: zero_count += 1
    by_count = sorted(counts,key=lambda merchant:(-counts[merchant],merchant))
    by_amount = sorted(amounts,key=lambda merchant:(-amounts[merchant],merchant))
    return {"merchant_count":len(counts),"record_count":sum(counts.values()),
        "positive_record_count":sum(positive_counts.values()),"positive_amount_sum_cents":sum(amounts.values()),
        "negative_record_count":negative_count,"negative_amount_sum_cents":negative_sum,"zero_record_count":zero_count,
        "top1_count_numerator":counts[by_count[0]] if by_count else 0,"count_denominator":sum(counts.values()),
        "top1_positive_amount_cents":amounts[by_amount[0]] if by_amount else 0,
        "positive_amount_denominator_cents":sum(amounts.values()),
        "top_by_count":[{"merchant_id":merchant,"record_count":counts[merchant]} for merchant in by_count[:3]],
        "top_by_positive_amount":[{"merchant_id":merchant,"positive_amount_cents":amounts[merchant]} for merchant in by_amount[:3]],
        "merchant_preview_truncated":len(counts)>3,
        "semantics":"Count includes positive/zero/negative records; amount shares use positive amounts only. Merchant IDs need not be real stores."}
