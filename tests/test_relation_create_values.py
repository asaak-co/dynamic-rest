from django.test import TestCase
from types import SimpleNamespace

from dynamic_rest.fields import DynamicRelationField
from dynamic_rest.metadata import DynamicMetadata
from dynamic_rest.serializers import DynamicModelSerializer
from dynamic_rest.viewsets import WithDynamicViewSetBase
from tests.models import User


class ChildSerializer(DynamicModelSerializer):
    class Meta:
        model = User
        name = 'child'
        fields = ('id', 'name')


class ParentSerializer(DynamicModelSerializer):
    child = DynamicRelationField(
        ChildSerializer,
        create=True,
        create_values={
            'product': {'from': 'product', 'action': 'default'},
            'company': {'from': 'company', 'action': 'set'},
        },
    )

    class Meta:
        model = User
        name = 'parent'
        fields = ('id', 'child')


class RelationCreateValuesTests(TestCase):
    def test_related_create_values_are_in_field_metadata(self):
        info = DynamicMetadata().get_resource_info(ParentSerializer())
        self.assertEqual(info['fields']['child']['create_values'], {
            'product': {'from': 'product', 'action': 'default'},
            'company': {'from': 'company', 'action': 'set'},
        })

    def test_default_preserves_input_and_set_overrides_it(self):
        parent = SimpleNamespace(product=SimpleNamespace(pk=7), company='A')
        field = SimpleNamespace(create_values={
            'product': {'from': 'product', 'action': 'default'},
            'company': {'from': 'company', 'action': 'set'},
        })
        submitted = {'product': 8, 'company': 'B'}
        result = WithDynamicViewSetBase._apply_create_values(
            submitted, parent, field
        )
        self.assertEqual(result, {'product': 8, 'company': 'A'})
        self.assertEqual(submitted, {'product': 8, 'company': 'B'})

        result = WithDynamicViewSetBase._apply_create_values(
            {'product': None}, parent, field
        )
        self.assertEqual(result, {'product': 7, 'company': 'A'})
