import pytest
from app.cli.fraud_live_acceptance import execute


@pytest.mark.parametrize('live,cap',[(False,'2'),(True,'3'),(True,'NaN'),(True,'0')])
def test_explicit_frozen_ceiling_before_files_credentials_or_client(tmp_path,live,cap):
    def forbidden():raise AssertionError('Client construction must not happen')
    with pytest.raises(ValueError,match='Explicit'):execute(tmp_path/'absent',live=live,max_estimated_cny=cap,client_factory=forbidden)
