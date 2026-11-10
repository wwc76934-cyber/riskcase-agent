"""Closed business roles for the new simulated source contract only."""
BUSINESS_ROLES={
    'authentication':{'authentication_event'},
    'device':{'device_event'},
    'account':{'account_event','identity_verification'},
    'customer':{'cardholder_statement','third_party_relay','contact_status'},
    'merchant':{'merchant_order','merchant_statement'},
    'procedure':{'procedure'},
    'reviewed_case':{'reviewed_method'},
}

def valid_role(source_type,role):
    return isinstance(source_type,str) and isinstance(role,str) and role in BUSINESS_ROLES.get(source_type,set())


def customer_statement_role(line):
    return line.get('business_role') in (None,'cardholder_statement')
