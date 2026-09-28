from django.test import TestCase

from dynamic_rest.metadata import DynamicMetadata
from dynamic_rest.serializers import DynamicModelSerializer
from tests.models import User


class PolicySerializer(DynamicModelSerializer):
    class Meta:
        model = User
        name = 'policy'
        fields = ('id', 'name')
        role = 'policy'
        role_metadata = {'pivot_resource': 'products', 'pivot_path': 'policy'}


class ResourceRoleTests(TestCase):
    def test_role_and_metadata_are_exposed_independently_of_permissions(self):
        info = DynamicMetadata().get_resource_info(PolicySerializer())
        self.assertEqual(info['role'], 'policy')
        self.assertEqual(
            info['role_metadata'],
            {'pivot_resource': 'products', 'pivot_path': 'policy'},
        )
