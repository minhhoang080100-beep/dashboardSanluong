"""Preview input must not validate successfully and then emit JSON Infinity."""
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

from backend.corporate_api.operation_contracts import OPERATION_MODELS
from test_corporate_operation_contracts import minimal_row


SIGNED_FIELDS = [
    ('berths', 'berthDepth'), ('berths', 'posFrom'), ('berths', 'posTo'),
    ('portWHYard', 'x'), ('portWHYard', 'y'),
    ('contwhYards', 'x'), ('contwhYards', 'y'),
]


@pytest.mark.parametrize('tail,field', SIGNED_FIELDS)
@pytest.mark.parametrize('value', [Decimal('1e400'), Decimal('-1e400'), '1e400', '-1e400'])
def test_finite_decimal_that_would_overflow_json_is_rejected(tail, field, value):
    model = OPERATION_MODELS['oprt.' + tail]
    with pytest.raises(ValidationError, match=field):
        model.model_validate({**minimal_row(tail), field: value})


@pytest.mark.parametrize('tail,field', SIGNED_FIELDS)
@pytest.mark.parametrize('value', [Decimal('-13.000'), Decimal('13.125'),
                                  Decimal('-1e300'), Decimal('1e300')])
def test_signed_finite_values_keep_their_sign_and_produce_valid_json(tail, field, value):
    model = OPERATION_MODELS['oprt.' + tail]
    row = model.model_validate({**minimal_row(tail), field: value})
    payload = row.model_dump(mode='json')
    encoded = json.dumps(payload, allow_nan=False)
    assert json.loads(encoded)[field] == float(value)
    assert getattr(row, field) == value
