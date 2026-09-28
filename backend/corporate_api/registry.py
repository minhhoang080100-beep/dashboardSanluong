"""Dataset registry shared by machine grants, publication and HTTP readers.

S contracts stay independent so adding operation catalogs cannot change existing
production payloads or silently expand scheduled extraction.
"""
from .contracts import MODELS as S_MODELS, IDENTITY as S_IDENTITY, PRODUCTION
from .operation_contracts import OPERATION_MODELS, OPERATION_IDENTITY

MODELS = {**S_MODELS, **OPERATION_MODELS}
IDENTITY = {**S_IDENTITY, **OPERATION_IDENTITY}


def resources_for_domain(domain):
    return {
        'production': tuple(S_MODELS),
        'operations': tuple(OPERATION_MODELS),
        'all': tuple(MODELS),
    }[domain]
