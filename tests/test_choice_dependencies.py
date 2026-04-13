from django.test import TestCase
from rest_framework import serializers

from dynamic_rest.metadata import DynamicMetadata
from dynamic_rest.serializers import DynamicModelSerializer
from tests.models import User


class CascadingChoiceSerializer(DynamicModelSerializer):
    """
    Test-only serializer: uses a real model, but adds two non-model fields
    to validate cascading choice behavior.
    """

    status = serializers.ChoiceField(choices=(("a", "A"), ("b", "B")))
    reason = serializers.ChoiceField(choices=(("r1", "R1"), ("r2", "R2")))

    class Meta:
        model = User
        fields = ("id", "name", "last_name", "status", "reason")
        choice_dependencies = {
            "reason": {
                "status": {
                    "a": ["r1"],
                    "b": ["r2"],
                },
            },
        }


class TestChoiceDependencies(TestCase):
    def test_metadata_exposes_choice_parent_and_mapping(self):
        serializer = CascadingChoiceSerializer()
        reason_field = serializer.get_all_fields()["reason"]
        info = DynamicMetadata().get_field_info(reason_field)

        self.assertEqual(info.get("choice_parent"), "status")
        self.assertEqual(
            info.get("choice_mapping"),
            {
                "a": ["r1"],
                "b": ["r2"],
            },
        )

    def test_validation_allows_allowed_child_for_parent(self):
        s = CascadingChoiceSerializer(
            data={"name": "n", "last_name": "ln", "status": "a", "reason": "r1"}
        )
        self.assertTrue(s.is_valid(), s.errors)

    def test_validation_rejects_disallowed_child_for_parent(self):
        s = CascadingChoiceSerializer(
            data={"name": "n", "last_name": "ln", "status": "a", "reason": "r2"}
        )
        self.assertFalse(s.is_valid())
        self.assertIn("reason", s.errors)

    def test_validation_requires_parent_when_child_is_set(self):
        s = CascadingChoiceSerializer(
            data={"name": "n", "last_name": "ln", "reason": "r1"}
        )
        self.assertFalse(s.is_valid())
        self.assertIn("status", s.errors)

