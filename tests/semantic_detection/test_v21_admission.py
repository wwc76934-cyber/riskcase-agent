import pytest

from app.risk.semantic_detection.v2.data_admission import decide


def clean():
    return {'source': 'explicit-test-fixture', 'evidence': {name: 'verified' for name in (
        'official_source', 'pinned_version', 'research_use_rights', 'business_text_semantics',
        'artifact_schema_and_fingerprints_verified', 'feature_allowlist_enforced',
        'text_and_semantic_fields_label_independent', 'all_model_inputs_label_independent', 'event_time_reliable',
        'text_strictly_available_before_target', 'entity_time_split_defined',
        'experiment_protocol_and_label_seal', 'explicit_static_scope',
        'normal_and_unlabeled_kept_distinct', 'historical_label_availability_known',
        'training_pool_only_optimization', 'known_training_ground_truth',
        'mask_and_availability_protocol_frozen', 'cold_start_no_full_label_weight_reuse')}}


def test_label_derived_semantics_cannot_pass_even_static_or_banned_risk_words():
    r=clean();r['evidence']['text_and_semantic_fields_label_independent']='failed'
    r['prompt_bans_risk_words']=True
    result=decide(r,gate_B_verified=True)
    assert not result['temporal_semantic_experiment_admitted']
    assert not result['static_semantic_experiment_admitted']


def test_future_only_text_can_be_explicitly_static_but_not_a_forecast():
    r=clean();r['evidence']['text_strictly_available_before_target']='failed'
    result=decide(r,gate_B_verified=True)
    assert not result['temporal_semantic_experiment_admitted']
    assert result['static_semantic_experiment_admitted']


def test_unknown_rights_or_unsealed_labels_or_unfinished_B_blocks_experiment():
    r=clean();r['evidence']['research_use_rights']='unknown'
    assert not decide(r,gate_B_verified=True)['static_semantic_experiment_admitted']
    r=clean();r['evidence']['experiment_protocol_and_label_seal']='unknown'
    assert not decide(r,gate_B_verified=True)['temporal_semantic_experiment_admitted']
    assert not decide(clean())['temporal_semantic_experiment_admitted']


def test_unknown_real_delays_do_not_become_normal_but_declared_mask_simulation_is_separate():
    r=clean();r['evidence']['historical_label_availability_known']='unknown'
    assert not decide(r,gate_B_verified=True)['real_unlabeled_SSL_experiment_admitted']
    r['label_availability_scope']='explicit_controlled_retrospective_simulation'
    result=decide(r,gate_B_verified=True)
    assert result['controlled_masking_experiment_admitted']
    assert not result['real_unlabeled_SSL_experiment_admitted']
    r['evidence']['normal_and_unlabeled_kept_distinct']='failed'
    assert not decide(r,gate_B_verified=True)['controlled_masking_experiment_admitted']
    r['evidence']['normal_and_unlabeled_kept_distinct']='verified'
    r['evidence']['cold_start_no_full_label_weight_reuse']='failed'
    assert not decide(r,gate_B_verified=True)['controlled_masking_experiment_admitted']


def test_string_false_or_other_implicit_truthiness_is_rejected():
    r=clean();r['evidence']['research_use_rights']='false'
    with pytest.raises(ValueError): decide(r,gate_B_verified=True)
    with pytest.raises(ValueError): decide(clean(),gate_B_verified='false')


def test_public_documentation_does_not_verify_unopened_dataset_bytes():
    r=clean();r['evidence']['artifact_schema_and_fingerprints_verified']='unknown'
    result=decide(r,gate_B_verified=True)
    assert not result['static_semantic_experiment_admitted']
    r=clean();r['evidence']['all_model_inputs_label_independent']='failed'
    r['label_availability_scope']='explicit_controlled_retrospective_simulation'
    assert not decide(r,gate_B_verified=True)['controlled_masking_experiment_admitted']
