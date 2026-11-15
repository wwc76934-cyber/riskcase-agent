from app.service.v3_paths import native_archive_path
from app.investigation.recording import RecordedClient
from tests.test_v3_reliability import Client

def test_response_archive_survives_long_windows_workspace_paths(tmp_path):
    directory=native_archive_path(tmp_path/('a'*70)/('b'*70)/('c'*70))
    client=RecordedClient(Client([dict(ok=True)]),directory)
    assert client.chat(messages=[])['content']
    assert (directory/'request-01.json').is_file() and (directory/'response-01.json').is_file()
