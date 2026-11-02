"""Conservative source gates; label-independent construction is not statistical X/Y independence."""


def decide(record, *, gate_B_verified=False):
    if not isinstance(record, dict): raise ValueError('A source-evidence record is required')
    if type(gate_B_verified) is not bool: raise ValueError('Gate B state must be an explicit boolean')
    evidence = record.get('evidence', {})
    statuses = {'verified', 'failed', 'unknown', 'not_applicable'}
    if any(value not in statuses for value in evidence.values()):
        raise ValueError('Use explicit evidence statuses, not truthy strings')
    has = lambda name: evidence.get(name) == 'verified'
    source_ok = all(has(name) for name in ('official_source', 'pinned_version', 'research_use_rights',
                                          'artifact_schema_and_fingerprints_verified', 'feature_allowlist_enforced'))
    label_free = has('text_and_semantic_fields_label_independent')
    eligible_text = has('business_text_semantics')
    split_ready = has('experiment_protocol_and_label_seal')
    normal_not_unknown = has('normal_and_unlabeled_kept_distinct')
    inputs_clean = has('all_model_inputs_label_independent')
    temporal_data = source_ok and inputs_clean and normal_not_unknown and label_free and eligible_text and all(has(name) for name in (
        'event_time_reliable', 'text_strictly_available_before_target', 'entity_time_split_defined'))
    static_data = source_ok and inputs_clean and normal_not_unknown and label_free and eligible_text and has('explicit_static_scope')
    real_ssl = source_ok and inputs_clean and normal_not_unknown and all(has(name) for name in (
        'event_time_reliable', 'historical_label_availability_known', 'training_pool_only_optimization'))
    mask_scope = record.get('label_availability_scope') == 'explicit_controlled_retrospective_simulation'
    controlled = source_ok and inputs_clean and normal_not_unknown and mask_scope and all(has(name) for name in (
        'event_time_reliable', 'known_training_ground_truth', 'mask_and_availability_protocol_frozen',
        'training_pool_only_optimization', 'cold_start_no_full_label_weight_reuse'))
    blockers = [name for name in ('official_source', 'pinned_version', 'research_use_rights',
        'artifact_schema_and_fingerprints_verified', 'feature_allowlist_enforced',
        'text_and_semantic_fields_label_independent', 'all_model_inputs_label_independent',
        'normal_and_unlabeled_kept_distinct', 'business_text_semantics') if not has(name)]
    result = {'source': record.get('source'), 'source_evidence_statuses': evidence,
        'temporal_semantic_data_eligible': temporal_data, 'static_semantic_data_eligible': static_data,
        'temporal_semantic_experiment_admitted': temporal_data and split_ready and gate_B_verified,
        'static_semantic_experiment_admitted': static_data and split_ready and gate_B_verified,
        'real_unlabeled_SSL_data_eligible': real_ssl, 'controlled_masking_data_eligible': controlled,
        'real_unlabeled_SSL_experiment_admitted': real_ssl and split_ready and gate_B_verified,
        'controlled_masking_experiment_admitted': controlled and split_ready and gate_B_verified,
        'semantic_blockers': blockers, 'gate_B_independently_verified': gate_B_verified,
        'experiment_protocol_and_label_seal_verified': split_ready}
    if not label_free:
        result['semantic_decision_note'] = 'Future-only aggregation may permit an explicitly static study; label-derived category/time/description features cannot be repaired by banning risk words or relabeling the study static.'
    if mask_scope:
        result['availability_limit'] = 'Controlled retrospective availability is an explicit simulation, not proof of real historical discovery delays.'
    return result
