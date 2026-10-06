from decimal import Decimal
from unittest.mock import patch

import pytest
from dynamic_rest.metadata import DynamicMetadata
from rest_framework import serializers
from rest_framework.renderers import JSONRenderer

from django.db import connection, models
from django.db.backends.postgresql.operations import DatabaseOperations
from dynamic_rest.serializers import DynamicModelSerializer


class NumericMetadataModel(models.Model):
    num_children_0_5 = models.PositiveIntegerField(null=True)

    class Meta:
        app_label = 'tests'
        managed = False


class NumericMetadataSerializer(DynamicModelSerializer):
    class Meta:
        model = NumericMetadataModel
        fields = ('num_children_0_5',)


def test_children_metadata_exposes_nonnegative_minimum():
    # Django 3.2 derives serializer bounds from the database backend. Exercise
    # PostgreSQL's range without requiring a database connection (SQLite has none).
    with patch.object(
        connection.ops, 'integer_field_range',
        side_effect=DatabaseOperations(None).integer_field_range,
    ):
        serializer = NumericMetadataSerializer()
        info = DynamicMetadata().get_field_info(
            serializer.get_field("num_children_0_5")
        )

    assert info["type"] == "integer"
    assert info["min_value"] == 0
    assert info["null"] is True


@pytest.mark.parametrize("field", [
    serializers.IntegerField(min_value=0, max_value=10),
    serializers.FloatField(min_value=-1.5, max_value=0),
    serializers.DecimalField(
        max_digits=5, decimal_places=2,
        min_value=Decimal("0.00"), max_value=Decimal("99.99"),
    ),
])
def test_numeric_bounds_are_preserved_and_json_serializable(field):
    info = DynamicMetadata().get_field_info(field)

    assert info["min_value"] == field.min_value
    assert info["max_value"] == field.max_value
    JSONRenderer().render(info)


@pytest.mark.parametrize("field", [
    serializers.IntegerField(),
    serializers.CharField(max_length=10),
])
def test_fields_without_numeric_bounds_do_not_gain_constraints(field):
    info = DynamicMetadata().get_field_info(field)

    assert "min_value" not in info
    assert "max_value" not in info



def test_dynamic_integer_field_preserves_step_style():
    from dynamic_rest.fields import DynamicIntegerField

    field = DynamicIntegerField(source='count', min_value=0, style={'step': 1})
    info = DynamicMetadata().get_field_info(field)

    assert info['style']['step'] == 1
    assert info['min_value'] == 0
