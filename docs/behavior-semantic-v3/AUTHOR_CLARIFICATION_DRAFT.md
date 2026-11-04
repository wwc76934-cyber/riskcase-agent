# Optional author inquiry — not sent

Subject: FraudNLP research use and recommended evaluation split

Dear Dr Boulieris,

Thank you for releasing FraudNLP and for your clarification in GitHub issue #4 permitting experimentation with the dataset. We are studying frozen language-model representations of banking API events, with TF-IDF and event-sequence baselines, entirely locally without uploading transaction records to external services.

Could you clarify whether there is a preferred citation or data-use license for reporting aggregate research results and, if applicable, sharing trained classifier weights without redistributing records?

Do you recommend a particular evaluation split, or provide split indices or irreversible anonymous grouping keys that would prevent transactions from the same user appearing in multiple subsets? We do not need identifying information. If absolute transaction timestamps cannot be shared, we will explicitly limit our conclusions to static sequence classification.

We would also appreciate clarification on whether negative labels mean no reported fraud, any available label-confirmation delay, and whether the terminal action and timing fields are observable before the target transaction is approved or completed.

Best regards,
[Your name / affiliation]

This draft requests clarification and optional evaluation metadata. It has not been sent and is not a prerequisite for the already explicitly permitted local static research. No commercial or data-redistribution permission is inferred.
