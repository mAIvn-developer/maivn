"""Public contract for the ticket-driven private-artifact SDK surface."""

from __future__ import annotations

import copy
import inspect
import pickle

import pytest

import maivn
from maivn import (
    Client,
    PrivateArtifactAuthorizationDeniedError,
    PrivateArtifactEffectFailedError,
    PrivateArtifactGrantExpiredError,
    PrivateArtifactsClient,
    PrivateArtifactTimeoutError,
    TransientVaultClientProof,
)
from maivn._internal import private_artifacts as private_artifact_module

_SHA256_HEX_LENGTH = 64


def test_client_exposes_ticket_driven_private_artifacts() -> None:
    """The public client owns the configured Data/Vault boundary."""
    client = Client(
        api_key='sdk-project-key',
        base_url='https://vault.example.test',
    )

    assert isinstance(client.private_artifacts, PrivateArtifactsClient)
    assert 'VaultIdentity' not in maivn.__all__
    assert not hasattr(private_artifact_module, 'VaultIdentity')


def test_retrieval_requires_an_opaque_sdk_generated_proof() -> None:
    """Callers bind the work to a digest without gaining raw-proof access."""
    client = Client(
        api_key='sdk-project-key',
        base_url='https://vault.example.test',
    )

    proof = client.private_artifacts.new_retrieval_proof()

    assert isinstance(proof, TransientVaultClientProof)
    assert len(proof.digest) == _SHA256_HEX_LENGTH
    assert set(proof.digest) <= set('0123456789abcdef')
    assert repr(proof) == 'TransientVaultClientProof(<redacted>)'
    assert str(proof) == 'TransientVaultClientProof(<redacted>)'
    assert not hasattr(proof, '__dict__')
    assert not hasattr(proof, 'raw')
    with pytest.raises(TypeError):
        copy.copy(proof)
    with pytest.raises(TypeError):
        copy.deepcopy(proof)
    with pytest.raises(TypeError):
        pickle.dumps(proof)


def test_private_artifact_methods_accept_work_or_exact_ref_action_selection() -> None:
    """Convenience selectors resolve exact refs without arbitrary artifact-id authority."""
    methods = {
        name: inspect.signature(getattr(PrivateArtifactsClient, name))
        for name in (
            'retrieve_stream',
            'aretrieve_stream',
            'export_redacted',
            'delete',
            'status',
        )
    }

    for name in ('export_redacted', 'delete'):
        assert tuple(methods[name].parameters)[:2] == ('self', 'work')
    assert tuple(methods['retrieve_stream'].parameters)[:3] == ('self', 'work', 'proof')
    assert tuple(methods['aretrieve_stream'].parameters)[:3] == ('self', 'work', 'proof')
    assert tuple(methods['status'].parameters) == ('self', 'server_intent_id')
    assert 'artifact_id' not in str(methods)
    assert methods['export_redacted'].parameters['filename'].kind is inspect.Parameter.KEYWORD_ONLY
    assert methods['export_redacted'].parameters['session_id'].default is None
    assert 'deletion_scope' not in str(methods['delete'])
    for retired in ('create', 'acreate', 'rework', 'arework'):
        assert not hasattr(PrivateArtifactsClient, retired)


def test_public_exports_are_pinned() -> None:
    """Private-artifact types remain importable from the SDK root."""
    assert {
        'PrivateArtifactAuthorizationDeniedError',
        'PrivateArtifactEffectFailedError',
        'PrivateArtifactError',
        'PrivateArtifactGrantExpiredError',
        'PrivateArtifactsClient',
        'PrivateArtifactTimeoutError',
        'TransientVaultClientProof',
    } <= set(maivn.__all__)

    for error_type in (
        PrivateArtifactAuthorizationDeniedError,
        PrivateArtifactEffectFailedError,
        PrivateArtifactGrantExpiredError,
        PrivateArtifactTimeoutError,
    ):
        error = error_type()
        assert repr(error) == f"{error_type.__name__}('{error.code}')"
        assert vars(error) == {'code': error.code}
