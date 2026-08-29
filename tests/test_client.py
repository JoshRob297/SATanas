import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from satanas.sat.client import CfdiPortalClient


def test_rfc_uppercase():
    c = CfdiPortalClient("abc123", "pw")
    assert c.rfc == "ABC123"


def test_state_file_none_when_missing(monkeypatch):
    monkeypatch.setattr("satanas.sat.client.config.CFDI_STATE_FILE", "/tmp/no_existe_xyz.json")
    c = CfdiPortalClient("RFC", "pw")
    assert c.storage_state is None
